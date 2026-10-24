"""Historical Recorder Bootstrap & Sensor Outlier Learner for Perceptive Lighting (v1.3.0).

Analyzes the past 7-14 days of Home Assistant Recorder history for indoor and
outdoor illuminance sensors alongside historical solar azimuth/elevation to:
1. Automatically detect which compass direction a room's windows or outdoor sensors face.
2. Detect High-Floor / Early Drop-Out sensors (sensors that drop to 0 lx early at dusk
   but provide valuable spatial data during brighter hours) and set `floor_lux` + `dropout_knee_lux`.
3. Detect Dead / Flatline / Obstructed outlier sensors (`base_trust_weight = 0.0`, `suggest_blacklist = True`).
4. Estimate per-sensor placement multipliers and initial location classification (`ai_location_guess`).
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


def _estimate_solar_position(
    hass: HomeAssistant, dt_utc: datetime
) -> tuple[float, float]:
    """Calculate solar elevation and azimuth for a historical UTC timestamp."""
    try:
        from homeassistant.helpers.sun import get_astral_location

        loc, elevation = get_astral_location(hass)
        sol_el = float(loc.solar_elevation(dt_utc, elevation))
        sol_az = float(loc.solar_azimuth(dt_utc))
        return sol_el, sol_az
    except Exception:  # noqa: BLE001
        lat = getattr(getattr(hass, "config", None), "latitude", 51.5)
        hour = dt_utc.hour + dt_utc.minute / 60.0
        az = (hour / 24.0) * 360.0
        max_el = max(15.0, 90.0 - abs(lat))
        el = max_el * math.sin(math.pi * (hour - 6.0) / 12.0)
        return el, az % 360.0


async def async_learn_sensor_profiles_from_history(
    hass: HomeAssistant,
    entity_ids: list[str],
    existing_profiles: dict[str, SensorLearnedProfile] | None = None,
    days_back: int = 10,
    is_outdoor: bool = False,
) -> dict[str, SensorLearnedProfile]:
    """Query Home Assistant Recorder history and compute learned profiles + outlier curves for lux sensors."""
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

    def _fetch_states() -> dict[str, list[Any]]:
        return get_significant_states(
            hass,
            start_time,
            end_time,
            valid_ids,
            minimal_response=True,
            no_attributes=True,
        )

    try:
        recorder_inst = get_instance(hass)
        history_map = await recorder_inst.async_add_executor_job(_fetch_states)
    except Exception as err:  # noqa: BLE001
        _LOGGER.debug("Could not fetch recorder history for %s: %s", valid_ids, err)
        return profiles

    sensor_daytime_medians: dict[str, float] = {}
    sensor_daytime_p95: dict[str, float] = {}
    sensor_twilight_zero_ratio: dict[str, float] = {}
    sensor_min_nonzero: dict[str, float] = {}

    for eid in valid_ids:
        states = history_map.get(eid, [])
        if not states:
            continue

        azimuth_bins: dict[int, list[float]] = {b: [] for b in range(12)}
        daytime_values: list[float] = []
        twilight_values: list[float] = []
        all_values: list[float] = []
        nonzero_values: list[float] = []

        for st in states:
            raw_state = getattr(st, "state", None)
            if raw_state in (None, "unknown", "unavailable"):
                continue
            try:
                lux_val = float(raw_state)
            except (ValueError, TypeError):
                continue

            if lux_val < 0.0:
                continue

            ts = getattr(st, "last_updated", None) or getattr(st, "last_changed", None)
            if ts is None:
                continue
            if ts.tzinfo is None:
                ts = ts.replace(tzinfo=timezone.utc)

            all_values.append(lux_val)
            if lux_val > 0.5:
                nonzero_values.append(lux_val)

            sol_el, sol_az = _estimate_solar_position(hass, ts)
            if sol_el > 8.0:
                daytime_values.append(lux_val)
                sin_el = max(0.25, math.sin(math.radians(min(85.0, sol_el))))
                normalized_lux = lux_val / sin_el
                bin_idx = int((sol_az % 360.0) // 30.0) % 12
                azimuth_bins[bin_idx].append(normalized_lux)
            elif -4.0 <= sol_el <= 8.0:
                twilight_values.append(lux_val)

        prof = profiles[eid]
        prof.samples_analyzed = len(all_values)

        if nonzero_values:
            s_nz = sorted(nonzero_values)
            sensor_min_nonzero[eid] = s_nz[max(0, int(len(s_nz) * 0.05))]

        if twilight_values:
            zero_cnt = sum(1 for v in twilight_values if v <= 1.0)
            sensor_twilight_zero_ratio[eid] = zero_cnt / len(twilight_values)

        if len(all_values) >= 6:
            s_all = sorted(all_values)
            p5_all = s_all[int(len(s_all) * 0.05)]
            p95_all = s_all[min(len(s_all) - 1, int(len(s_all) * 0.95))]
            # Flatline / stuck / obstructed sensor detection
            if (p95_all - p5_all) <= 1.0 and p95_all <= 2.0:
                prof.base_trust_weight = 0.0
                prof.suggest_blacklist = True
                prof.ai_location_guess = "Flatlined / Obstructed (0-2 lx constant — Recommend Blacklist)"
                continue

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
                ratio = peak_val / max(1.0, baseline_val)
                prof.direct_sun_ratio = round(clamp(ratio, 1.0, 5.0), 2)
                if ratio >= 1.28 or is_outdoor:
                    prof.peak_solar_azimuth = round(best_bin * 30.0 + 15.0, 1)

    # Multi-sensor comparison & High-Floor / Early Drop-Out Detection
    if not is_outdoor and sensor_daytime_medians:
        med_values = list(sensor_daytime_medians.values())
        room_geo_mean = math.exp(sum(math.log(v) for v in med_values) / len(med_values))

        for eid, s_med in sensor_daytime_medians.items():
            prof = profiles[eid]
            if prof.suggest_blacklist:
                continue

            if len(sensor_daytime_medians) >= 2 and prof.placement == "auto_learn":
                raw_ratio = room_geo_mean / max(1.0, s_med)
                prof.learned_multiplier = round(
                    clamp(math.pow(raw_ratio, 0.55), 0.55, 1.85), 3
                )

            # Check for High Dark-Floor / Early Drop-Out behavior:
            # If sensor jumps from 0 directly to >= 4 lx OR spends >55% of civil twilight at <=1 lx
            # while having a healthy daytime p95 (>35 lx), configure smooth floor tapering!
            min_nz = sensor_min_nonzero.get(eid, 1.0)
            twi_zero = sensor_twilight_zero_ratio.get(eid, 0.0)
            p95_day = sensor_daytime_p95.get(eid, 20.0)

            if p95_day >= 25.0 and (min_nz >= 4.0 or twi_zero >= 0.55):
                prof.floor_lux = round(max(1.0, min_nz * 0.6), 1)
                prof.dropout_knee_lux = round(max(10.0, min_nz * 2.5, prof.floor_lux + 8.0), 1)
                if not prof.ai_location_guess:
                    prof.ai_location_guess = (
                        f"High Dark-Floor Sensor (tapers below {prof.dropout_knee_lux} lx)"
                    )
            elif not prof.ai_location_guess:
                if prof.direct_sun_ratio >= 1.8:
                    prof.ai_location_guess = "Near Window (Direct Solar Glare Peak)"
                elif prof.learned_multiplier >= 1.25:
                    prof.ai_location_guess = "Dim Interior / Shaded Corner"
                elif prof.learned_multiplier <= 0.78:
                    prof.ai_location_guess = "Bright Exposure / Near Glazing"
                else:
                    prof.ai_location_guess = "Balanced Room Interior"

    return profiles
