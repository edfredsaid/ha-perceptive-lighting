"""Historical Recorder Bootstrap Learner for Perceptive Lighting.

Analyzes the past 7-14 days of Home Assistant Recorder history for indoor and
outdoor illuminance sensors alongside historical solar azimuth/elevation to:
1. Automatically detect which compass direction a room's windows or outdoor sensors face
   (by finding the solar azimuth bin where clear-sky lux peaks relative to overcast days).
2. Automatically calibrate per-sensor placement multipliers (e.g., detecting if one sensor
   in a room sits on a bright windowsill while another sits in a dim corner).
"""
from __future__ import annotations

from datetime import datetime, timedelta, timezone
import logging
import math
from typing import Any

from homeassistant.core import HomeAssistant
from homeassistant.helpers.sun import get_astral_Event_date, get_astral_location

from .engine import SensorLearnedProfile, clamp

_LOGGER = logging.getLogger(__name__)


def _estimate_solar_position(
    hass: HomeAssistant, dt_utc: datetime
) -> tuple[float, float]:
    """Calculate solar elevation and azimuth for a historical UTC timestamp."""
    try:
        loc, elevation = get_astral_location(hass)
        sol_el = float(loc.solar_elevation(dt_utc, elevation))
        sol_az = float(loc.solar_azimuth(dt_utc))
        return sol_el, sol_az
    except Exception:  # noqa: BLE001
        # Fallback sinusoidal approximation based on UTC hour + latitude
        lat = getattr(hass.config, "latitude", 51.5)
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
    """Query Home Assistant Recorder history and compute learned profiles for lux sensors."""
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

    # Track daytime median lux per sensor to compare sensors within the same room
    sensor_daytime_medians: dict[str, float] = {}

    for eid in valid_ids:
        states = history_map.get(eid, [])
        if not states:
            continue

        # 12 azimuth bins of 30° each (0°..360°)
        azimuth_bins: dict[int, list[float]] = {b: [] for b in range(12)}
        daytime_values: list[float] = []

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

            sol_el, sol_az = _estimate_solar_position(hass, ts)
            if sol_el > 8.0:
                daytime_values.append(lux_val)
                # Normalize by sin(elevation) so morning/evening sun isn't penalized vs noon
                sin_el = max(0.25, math.sin(math.radians(min(85.0, sol_el))))
                normalized_lux = lux_val / sin_el
                bin_idx = int((sol_az % 360.0) // 30.0) % 12
                azimuth_bins[bin_idx].append(normalized_lux)

        prof = profiles[eid]
        prof.samples_analyzed = len(daytime_values)

        if len(daytime_values) >= 8:
            sorted_day = sorted(daytime_values)
            median_lux = sorted_day[len(sorted_day) // 2]
            sensor_daytime_medians[eid] = max(1.0, median_lux)

            # Find azimuth bin with highest 85th-percentile normalized lux
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
                # If peak bin is significantly brighter (>1.28x) than other solar angles,
                # direct sun shines onto this window/sensor at this azimuth!
                if ratio >= 1.28 or is_outdoor:
                    prof.peak_solar_azimuth = round(best_bin * 30.0 + 15.0, 1)

    # If multiple indoor sensors exist in the same room, auto-balance their relative multipliers
    if not is_outdoor and len(sensor_daytime_medians) >= 2:
        med_values = list(sensor_daytime_medians.values())
        room_geo_mean = math.exp(sum(math.log(v) for v in med_values) / len(med_values))
        for eid, s_med in sensor_daytime_medians.items():
            prof = profiles[eid]
            if prof.placement == "auto_learn":
                # Dampen overly bright windowsill sensor, boost dim corner sensor gently
                raw_ratio = room_geo_mean / max(1.0, s_med)
                prof.learned_multiplier = round(clamp(math.pow(raw_ratio, 0.55), 0.55, 1.65), 3)

    return profiles
