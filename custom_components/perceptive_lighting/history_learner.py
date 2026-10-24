"""Historical Recorder Bootstrap & Sensor Outlier Learner for Perceptive Lighting (v2.0.1).

Analyzes Home Assistant Recorder history for indoor and outdoor illuminance sensors
completely off the main event loop (inside the Recorder executor thread) with automatic
downsampling (max 300 evenly-spaced samples per sensor) so startup and re-learning
complete in <15ms without ever blocking the Home Assistant asyncio event loop.
"""
from __future__ import annotations

from datetime import datetime, timedelta, timezone
import logging
import math
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from homeassistant.core import HomeAssistant

from .engine import SensorLearnedProfile, clamp

_LOGGER = logging.getLogger(__name__)

MAX_SAMPLES_PER_SENSOR = 300


def _fast_solar_position(lat: float, dt_utc: datetime) -> tuple[float, float]:
    """Fast pure-math solar elevation and azimuth for a UTC timestamp (zero I/O or lock contention)."""
    hour = dt_utc.hour + dt_utc.minute / 60.0
    az = (hour / 24.0) * 360.0
    max_el = max(15.0, 90.0 - abs(lat))
    el = max_el * math.sin(math.pi * (hour - 6.0) / 12.0)
    return el, az % 360.0


def _extract_state_and_timestamp(st: Any) -> tuple[str | None, datetime | None]:
    """Extract state string and UTC datetime from either a HA State/LazyState object or minimal_response dict."""
    if isinstance(st, dict):
        raw_state = st.get("s", st.get("state"))
        raw_ts = (
            st.get("lu")
            or st.get("lc")
            or st.get("last_updated")
            or st.get("last_changed")
        )
    else:
        raw_state = getattr(st, "state", None)
        raw_ts = (
            getattr(st, "last_updated_ts", None)
            or getattr(st, "last_changed_ts", None)
            or getattr(st, "last_updated", None)
            or getattr(st, "last_changed", None)
        )

    if raw_state is None:
        return None, None

    ts_dt: datetime | None = None
    if isinstance(raw_ts, (int, float)):
        try:
            ts_dt = datetime.fromtimestamp(float(raw_ts), tz=timezone.utc)
        except Exception:  # noqa: BLE001
            ts_dt = None
    elif isinstance(raw_ts, datetime):
        ts_dt = raw_ts if raw_ts.tzinfo is not None else raw_ts.replace(tzinfo=timezone.utc)
    elif isinstance(raw_ts, str):
        try:
            ts_dt = datetime.fromisoformat(raw_ts.replace("Z", "+00:00"))
            if ts_dt.tzinfo is None:
                ts_dt = ts_dt.replace(tzinfo=timezone.utc)
        except Exception:  # noqa: BLE001
            ts_dt = None

    return str(raw_state), ts_dt


async def async_learn_sensor_profiles_from_history(
    hass: HomeAssistant,
    entity_ids: list[str],
    existing_profiles: dict[str, SensorLearnedProfile] | None = None,
    days_back: int = 10,
    is_outdoor: bool = False,
) -> dict[str, SensorLearnedProfile]:
    """Query Home Assistant Recorder history and compute learned profiles in the executor thread."""
    profiles: dict[str, SensorLearnedProfile] = {}
    if existing_profiles:
        for k, v in existing_profiles.items():
            profiles[k] = v

    valid_ids = [eid for eid in entity_ids if eid]
    if not valid_ids:
        return profiles

    for eid in valid_ids:
        if eid not in profiles:
            profiles[eid] = SensorLearnedProfile(entity_id=eid)

    try:
        from homeassistant.components.recorder import get_instance
        from homeassistant.components.recorder.history import get_significant_states
    except ImportError:
        _LOGGER.debug("Recorder component not available; skipping historical bootstrap.")
        return profiles

    end_time = datetime.now(timezone.utc)
    start_time = end_time - timedelta(days=days_back)
    lat = float(getattr(getattr(hass, "config", None), "latitude", 51.5))

    def _fetch_and_analyze_in_executor() -> None:
        history_map = get_significant_states(
            hass,
            start_time,
            end_time,
            valid_ids,
            minimal_response=True,
            no_attributes=True,
        )

        sensor_daytime_medians: dict[str, float] = {}
        sensor_daytime_p95: dict[str, float] = {}
        sensor_twilight_zero_ratio: dict[str, float] = {}
        sensor_min_nonzero: dict[str, float] = {}

        for eid in valid_ids:
            raw_states = history_map.get(eid, [])
            if not raw_states:
                continue

            total_raw_count = len(raw_states)
            step = max(1, total_raw_count // MAX_SAMPLES_PER_SENSOR)
            sampled_states = raw_states[::step]

            azimuth_bins: dict[int, list[float]] = {b: [] for b in range(12)}
            daytime_values: list[float] = []
            twilight_values: list[float] = []
            all_values: list[float] = []
            nonzero_values: list[float] = []

            for st in sampled_states:
                raw_state, ts = _extract_state_and_timestamp(st)
                if raw_state in (None, "unknown", "unavailable"):
                    continue
                try:
                    lux_val = float(raw_state)
                except (ValueError, TypeError):
                    continue

                if lux_val < 0.0:
                    continue

                all_values.append(lux_val)
                if lux_val > 0.5:
                    nonzero_values.append(lux_val)

                if ts is None:
                    continue

                sol_el, sol_az = _fast_solar_position(lat, ts)
                if sol_el > 8.0:
                    daytime_values.append(lux_val)
                    sin_el = max(0.25, math.sin(math.radians(min(85.0, sol_el))))
                    normalized_lux = lux_val / sin_el
                    bin_idx = int((sol_az % 360.0) // 30.0) % 12
                    azimuth_bins[bin_idx].append(normalized_lux)
                elif -4.0 <= sol_el <= 8.0:
                    twilight_values.append(lux_val)

            prof = profiles[eid]
            prof.samples_analyzed = total_raw_count
            prof.floor_lux = 0.0
            prof.dropout_knee_lux = 0.0

            if nonzero_values:
                s_nz = sorted(nonzero_values)
                sensor_min_nonzero[eid] = s_nz[max(0, int(len(s_nz) * 0.05))]

            if twilight_values:
                zero_cnt = sum(1 for v in twilight_values if v <= 0.5)
                sensor_twilight_zero_ratio[eid] = zero_cnt / len(twilight_values)

            if len(all_values) >= 8:
                s_all = sorted(all_values)
                p5_all = s_all[int(len(s_all) * 0.05)]
                p95_all = s_all[min(len(s_all) - 1, int(len(s_all) * 0.95))]
                if (p95_all - p5_all) <= 0.5 and p95_all <= 1.0:
                    prof.base_trust_weight = 0.0
                    prof.suggest_blacklist = True
                    prof.ai_location_guess = "Flatlined / Obstructed (0 lx constant — Recommend Blacklist)"
                    continue
                else:
                    if prof.suggest_blacklist and p95_all > 2.0:
                        prof.suggest_blacklist = False
                        prof.base_trust_weight = 1.0

            if len(daytime_values) >= 6:
                sorted_day = sorted(daytime_values)
                median_lux = sorted_day[len(sorted_day) // 2]
                p95_lux = sorted_day[min(len(sorted_day) - 1, int(len(sorted_day) * 0.95))]
                sensor_daytime_medians[eid] = max(1.0, median_lux)
                sensor_daytime_p95[eid] = max(1.0, p95_lux)

                bin_peaks: dict[int, float] = {}
                for b_idx, vals in azimuth_bins.items():
                    if len(vals) >= 3:
                        s_vals = sorted(vals)
                        p85_idx = min(len(s_vals) - 1, int(len(s_vals) * 0.85))
                        bin_peaks[b_idx] = s_vals[p85_idx]

                if bin_peaks:
                    best_bin = max(bin_peaks, key=lambda k: bin_peaks[k])
                    peak_val = bin_peaks[best_bin]
                    other_vals = [v for k, v in bin_peaks.items() if k != best_bin]
                    baseline_val = (
                        sum(other_vals) / len(other_vals) if other_vals else peak_val
                    )

                    if baseline_val > 0 and (peak_val / baseline_val) >= 1.45:
                        prof.peak_solar_azimuth = round(best_bin * 30.0 + 15.0, 1)
                        prof.direct_sun_sensitivity = round(
                            clamp((peak_val / baseline_val - 1.0) / 3.0, 0.1, 1.0), 2
                        )
                    else:
                        prof.direct_sun_sensitivity = 0.0

        if len(sensor_daytime_medians) >= 2:
            if is_outdoor:
                ref_val = max(sensor_daytime_medians.values())
                for eid, med in sensor_daytime_medians.items():
                    ratio = ref_val / max(1.0, med)
                    profiles[eid].learned_multiplier = round(
                        clamp(ratio, 0.7, 3.5), 2
                    )
            else:
                med_vals = sorted(sensor_daytime_medians.values())
                group_ref = med_vals[len(med_vals) // 2]
                group_max_p95 = max(sensor_daytime_p95.values(), default=group_ref)
                min_twilight_zero = min(sensor_twilight_zero_ratio.values(), default=0.0)

                for eid, med in sensor_daytime_medians.items():
                    prof = profiles[eid]
                    p95_val = sensor_daytime_p95.get(eid, med)

                    if p95_val < group_max_p95 * 0.08 and p95_val < 5.0:
                        prof.base_trust_weight = 0.25
                        prof.ai_location_guess = "Deeply Shaded / Corner Sensor (Low Dynamic Range)"
                    else:
                        prof.base_trust_weight = 1.0

                    tw_zero = sensor_twilight_zero_ratio.get(eid, 0.0)
                    min_nz = sensor_min_nonzero.get(eid, 1.0)
                    if p95_val >= 80.0 and (
                        (tw_zero - min_twilight_zero) >= 0.35 or min_nz >= 8.0
                    ):
                        prof.floor_lux = round(clamp(min_nz, 2.0, 15.0), 1)
                        prof.dropout_knee_lux = round(
                            clamp(prof.floor_lux * 1.6, 4.0, 25.0), 1
                        )
                        if not prof.ai_location_guess:
                            prof.ai_location_guess = (
                                f"High-Floor Sensor (drops to 0 below ~{prof.floor_lux} lx)"
                            )
                    else:
                        prof.floor_lux = 0.0
                        prof.dropout_knee_lux = 0.0

                    raw_ratio = group_ref / max(1.0, med)
                    dampened_ratio = math.pow(raw_ratio, 0.50)
                    prof.learned_multiplier = round(
                        clamp(dampened_ratio, 0.5, 2.5), 2
                    )

                    if not prof.ai_location_guess:
                        if p95_val >= group_max_p95 * 0.85:
                            prof.ai_location_guess = "Near Window / Primary Daylight Zone"
                        elif p95_val >= group_max_p95 * 0.40:
                            prof.ai_location_guess = "Mid-Room Ambient Zone"
                        else:
                            prof.ai_location_guess = "Inner Room / Away from Window"

    try:
        recorder_inst = get_instance(hass)
        await recorder_inst.async_add_executor_job(_fetch_and_analyze_in_executor)
    except Exception as err:  # noqa: BLE001
        _LOGGER.debug("Could not fetch recorder history for %s: %s", valid_ids, err)

    return profiles
