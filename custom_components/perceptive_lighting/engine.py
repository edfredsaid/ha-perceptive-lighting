"""Psychophysical & Architectural Perceptive Illuminance Engine.

Implements:
1. Weber-Fechner Logarithmic Brightness Compression
2. Outdoor Visual Adaptation & Simultaneous Window Contrast ("The Cave Effect")
3. Kit Cuttle's Mean Room Surface Exitance (MRSE) Direct vs. Diffuse Sky Factor
4. Multi-Sensor Internal Contrast Gradient Detection
5. Kruithof Curve & Circadian Color Temperature (CCT) Mapping
6. Historical Recorder Bootstrap for Window Azimuth & Outdoor Sensor Orientation
"""
from __future__ import annotations

from dataclasses import dataclass, field
import math
from typing import Any

from .const import (
    ACTIVITY_DEFINITIONS,
    ACTIVITY_RELAXING,
    DAYLIGHT_ADAPTATION_MULTIPLIERS,
    DAYLIGHT_MEDIUM,
    DAYLIGHT_NONE,
    PLACEMENT_MULTIPLIERS,
    STATE_BRIGHT,
    STATE_COMFORTABLE,
    STATE_DARK,
    STATE_DIM,
    SUN_DIR_COMPASS,
    SUN_DIR_EVENING,
    SUN_DIR_MIDDAY,
    SUN_DIR_MORNING,
    SUN_DIR_NEVER,
)


# Weather condition to Diffuse Sky Factor & Cloud Cover Estimate
WEATHER_CONDITION_MAP: dict[str, tuple[float, float]] = {
    # condition: (estimated_cloud_cover_0_to_1, diffuse_sky_factor)
    "clear-night": (0.0, 1.0),
    "sunny": (0.10, 0.88),  # High direct beam -> harsh shadows/glare indoors
    "partlycloudy": (0.45, 1.04),
    "cloudy": (0.85, 1.18),  # High isotropic sky diffusion -> wraps room walls evenly (high MRSE)
    "fog": (0.95, 1.20),
    "rainy": (0.90, 1.12),
    "pouring": (0.98, 1.08),
    "snowy": (0.85, 1.25),  # High ground albedo + diffuse sky
    "snowy-rainy": (0.90, 1.15),
    "windy": (0.40, 1.0),
    "windy-variant": (0.60, 1.05),
    "lightning": (0.95, 1.05),
    "lightning-rainy": (0.95, 1.05),
    "hail": (0.95, 1.05),
    "exceptional": (0.50, 1.0),
}


@dataclass
class SensorLearnedProfile:
    """Learned or configured calibration profile for an individual lux sensor."""

    entity_id: str
    placement: str = "auto_learn"
    learned_multiplier: float = 1.0
    peak_solar_azimuth: float | None = None  # Azimuth (0-360) when direct sun hits sensor
    direct_sun_ratio: float = 1.0  # Ratio of sunny-to-cloudy lux at peak azimuth
    samples_analyzed: int = 0

    def to_dict(self) -> dict[str, Any]:
        return {
            "entity_id": self.entity_id,
            "placement": self.placement,
            "learned_multiplier": round(self.learned_multiplier, 3),
            "peak_solar_azimuth": (
                round(self.peak_solar_azimuth, 1)
                if self.peak_solar_azimuth is not None
                else None
            ),
            "direct_sun_ratio": round(self.direct_sun_ratio, 2),
            "samples_analyzed": self.samples_analyzed,
        }

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> SensorLearnedProfile:
        return cls(
            entity_id=data.get("entity_id", ""),
            placement=data.get("placement", "auto_learn"),
            learned_multiplier=float(data.get("learned_multiplier", 1.0)),
            peak_solar_azimuth=data.get("peak_solar_azimuth"),
            direct_sun_ratio=float(data.get("direct_sun_ratio", 1.0)),
            samples_analyzed=int(data.get("samples_analyzed", 0)),
        )


@dataclass
class OutdoorEnvironmentState:
    """Represents the live outdoor lighting, weather, and solar state."""

    raw_outdoor_lux: float = 0.0
    effective_sky_lux: float = 0.0
    is_synthetic_lux: bool = False
    weather_condition: str = "cloudy"
    cloud_cover_ratio: float = 0.5
    diffuse_sky_factor: float = 1.0
    solar_elevation: float = 0.0
    solar_azimuth: float = 180.0
    is_daytime: bool = True
    circadian_factor: float = 1.0
    sky_quality: str = "diffuse"  # "direct_sun", "diffuse", "twilight", "night"


@dataclass
class RoomPerceptiveResult:
    """Complete output of the Perceptive Lighting calculation for a room."""

    raw_lux: float = 0.0
    effective_spatial_lux: float = 0.0
    natural_spatial_lux: float = 0.0
    artificial_lux_contribution: float = 0.0
    target_comfort_lux: float = 80.0
    perceptive_illuminance_pct: float = 0.0
    natural_perceptive_illuminance_pct: float = 0.0
    lighting_deficit_pct: float = 100.0
    perceptive_state: str = STATE_DARK
    diffuse_modifier: float = 1.0
    direct_sun_in_window: bool = False
    internal_contrast_ratio: float = 1.0
    outdoor_adaptation_offset_lux: float = 0.0
    recommended_brightness_pct: int = 0
    recommended_cct_kelvin: int = 2700
    active_activity: str = ACTIVITY_RELAXING
    sensor_breakdown: dict[str, float] = field(default_factory=dict)


def clamp(val: float, low: float, high: float) -> float:
    """Clamp a value between low and high."""
    return max(low, min(high, val))


def estimate_synthetic_outdoor_lux(
    solar_elevation: float, cloud_cover_ratio: float
) -> float:
    """Estimate outdoor illuminance (lux) from solar elevation and cloud cover."""
    if solar_elevation <= -6.0:
        # Astronomical/nautical night
        return 0.1
    if solar_elevation <= 0.0:
        # Civil twilight (-6° to 0°): 1 lx to ~400 lx
        t = (solar_elevation + 6.0) / 6.0
        base_twilight = 1.0 * math.pow(400.0, t)
        return base_twilight * (1.0 - 0.55 * cloud_cover_ratio)

    # Daytime (0° to 90°): sinusoidal air-mass approximation up to ~95,000 lx clear sky
    sin_el = math.sin(math.radians(clamp(solar_elevation, 0.5, 90.0)))
    clear_sky_lux = 95000.0 * math.pow(sin_el, 1.35) + 400.0
    # Overcast sky reduces total horizontal illuminance up to ~80-85%
    cloud_attenuation = 1.0 - 0.80 * math.pow(clamp(cloud_cover_ratio, 0.0, 1.0), 1.3)
    return max(10.0, clear_sky_lux * cloud_attenuation)


def compute_outdoor_environment(
    sensor_readings: dict[str, float],
    sensor_profiles: dict[str, SensorLearnedProfile],
    weather_condition: str | None,
    weather_cloud_coverage: float | None,
    solar_elevation: float,
    solar_azimuth: float,
) -> OutdoorEnvironmentState:
    """Compute unified outdoor illuminance, accounting for direct sun vs shade on outdoor sensors."""
    cond = (weather_condition or "cloudy").lower()
    est_clouds, base_diffuse = WEATHER_CONDITION_MAP.get(cond, (0.5, 1.0))

    if weather_cloud_coverage is not None:
        cloud_ratio = clamp(float(weather_cloud_coverage) / 100.0, 0.0, 1.0)
        # Blend weather condition diffusion with explicit cloud coverage percentage
        diffuse_factor = 0.85 + 0.35 * math.sin(cloud_ratio * math.pi * 0.75)
        diffuse_factor = (diffuse_factor + base_diffuse) / 2.0
    else:
        cloud_ratio = est_clouds
        diffuse_factor = base_diffuse

    synthetic_lux = estimate_synthetic_outdoor_lux(solar_elevation, cloud_ratio)

    if not sensor_readings:
        raw_lux = synthetic_lux
        effective_lux = synthetic_lux
        is_synthetic = True
    else:
        is_synthetic = False
        values = list(sensor_readings.values())
        raw_lux = sum(values) / len(values)

        # Correct for outdoor sensors sitting in direct solar beam vs building shadow
        corrected_values: list[float] = []
        for eid, val in sensor_readings.items():
            prof = sensor_profiles.get(eid)
            if (
                prof
                and prof.peak_solar_azimuth is not None
                and solar_elevation > 8.0
                and cloud_ratio < 0.45
            ):
                az_diff = abs((solar_azimuth - prof.peak_solar_azimuth + 180) % 360 - 180)
                if az_diff < 45.0:
                    # Sensor is receiving direct beam glare; normalize toward diffuse sky dome
                    glare_damp = clamp(1.0 / max(1.0, math.sqrt(prof.direct_sun_ratio)), 0.55, 1.0)
                    corrected_values.append(val * glare_damp)
                elif az_diff > 110.0 and val < synthetic_lux * 0.35:
                    # Sensor is on the shaded side of the house during a sunny day; blend with sky model
                    corrected_values.append(val * 0.65 + synthetic_lux * 0.35)
                else:
                    corrected_values.append(val)
            else:
                corrected_values.append(val)

        effective_lux = max(corrected_values) if len(corrected_values) > 1 else corrected_values[0]

    # Circadian expectation factor (0.55 at night -> 1.0 midday)
    if solar_elevation <= -6.0:
        circadian = 0.55
        sky_quality = "night"
    elif solar_elevation <= 2.0:
        circadian = 0.65 + 0.20 * ((solar_elevation + 6.0) / 8.0)
        sky_quality = "twilight"
    else:
        # Smooth diurnal + seasonal elevation curve
        el_norm = clamp(solar_elevation / 55.0, 0.0, 1.0)
        circadian = 0.85 + 0.25 * math.pow(el_norm, 0.7)
        sky_quality = "direct_sun" if (cloud_ratio < 0.35 and solar_elevation > 10.0) else "diffuse"

    return OutdoorEnvironmentState(
        raw_outdoor_lux=round(raw_lux, 1),
        effective_sky_lux=round(effective_lux, 1),
        is_synthetic_lux=is_synthetic,
        weather_condition=cond,
        cloud_cover_ratio=round(cloud_ratio, 2),
        diffuse_sky_factor=round(diffuse_factor, 3),
        solar_elevation=round(solar_elevation, 1),
        solar_azimuth=round(solar_azimuth, 1),
        is_daytime=solar_elevation > 0.0,
        circadian_factor=round(circadian, 3),
        sky_quality=sky_quality,
    )


def is_sun_in_room_window(
    sun_direction_setting: str,
    configured_window_azimuth: float | None,
    learned_window_azimuth: float | None,
    solar_azimuth: float,
    solar_elevation: float,
    cloud_cover_ratio: float,
) -> bool:
    """Determine if direct solar beams are currently shining into the room's windows."""
    if solar_elevation <= 4.0 or cloud_cover_ratio > 0.65:
        return False
    if sun_direction_setting == SUN_DIR_NEVER:
        return False

    target_az: float | None = None
    half_cone = 52.0

    if sun_direction_setting == SUN_DIR_COMPASS and configured_window_azimuth is not None:
        target_az = configured_window_azimuth
    elif sun_direction_setting == SUN_DIR_MORNING:
        target_az = 95.0
    elif sun_direction_setting == SUN_DIR_MIDDAY:
        target_az = 180.0
    elif sun_direction_setting == SUN_DIR_EVENING:
        target_az = 265.0
    elif learned_window_azimuth is not None:
        target_az = learned_window_azimuth

    if target_az is None:
        return False

    az_diff = abs((solar_azimuth - target_az + 180.0) % 360.0 - 180.0)
    return az_diff <= half_cone


def compute_room_perceptive_illuminance(
    indoor_sensor_readings: dict[str, float],
    sensor_profiles: dict[str, SensorLearnedProfile],
    outdoor: OutdoorEnvironmentState,
    activity_key: str = ACTIVITY_RELAXING,
    daylight_level: str = DAYLIGHT_MEDIUM,
    sun_window_direction: str = "auto_learn",
    window_azimuth: float | None = None,
    artificial_lux_estimate: float = 0.0,
    feel_nudge_day_pct: float = 0.0,
    feel_nudge_night_pct: float = 0.0,
    learned_activity_mult: float = 1.0,
    learned_outdoor_adaptation_scale: float = 1.0,
    learned_diffuse_sensitivity: float = 1.0,
    learned_night_scale: float = 1.0,
    learned_brightness_scale: float = 1.0,
) -> RoomPerceptiveResult:
    """Compute Perceptive Illuminance, Natural Perceptive Illuminance, and Deficit for a room."""
    activity = ACTIVITY_DEFINITIONS.get(activity_key, ACTIVITY_DEFINITIONS[ACTIVITY_RELAXING])
    daylight_mult = DAYLIGHT_ADAPTATION_MULTIPLIERS.get(daylight_level, 1.0)

    # 1. Process individual indoor lux sensors with per-sensor placement calibration
    calibrated_readings: dict[str, float] = {}
    learned_azimuths: list[float] = []

    for eid, raw_val in indoor_sensor_readings.items():
        prof = sensor_profiles.get(eid)
        placement = prof.placement if prof else "balanced"
        mult = PLACEMENT_MULTIPLIERS.get(placement, 1.0)
        if prof and placement == "auto_learn":
            mult = prof.learned_multiplier
        if prof and prof.peak_solar_azimuth is not None:
            learned_azimuths.append(prof.peak_solar_azimuth)
        calibrated_readings[eid] = max(0.0, raw_val * mult)

    avg_learned_az = (
        sum(learned_azimuths) / len(learned_azimuths) if learned_azimuths else None
    )

    if calibrated_readings:
        raw_vals = list(indoor_sensor_readings.values())
        raw_lux = sum(raw_vals) / len(raw_vals)
        cal_vals = list(calibrated_readings.values())
        base_spatial_lux = sum(cal_vals) / len(cal_vals)

        # Multi-sensor internal contrast detection:
        # If one sensor (near window) is much brighter than an interior sensor,
        # the room has a steep brightness gradient (high contrast = lower perceived MRSE).
        if len(cal_vals) >= 2:
            min_v = max(1.0, min(cal_vals))
            max_v = max(1.0, max(cal_vals))
            internal_contrast = max_v / min_v
            # Weight toward the dimmer interior (geometric mean blend) when contrast is high
            if internal_contrast > 3.0:
                geo_mean = math.sqrt(min_v * max_v)
                base_spatial_lux = 0.65 * geo_mean + 0.35 * base_spatial_lux
        else:
            internal_contrast = 1.0
    else:
        # Windowless / virtual room fallback
        raw_lux = artificial_lux_estimate
        base_spatial_lux = artificial_lux_estimate
        internal_contrast = 1.0

    # 2. Direct vs. Diffuse Sky Factor (MRSE modeling + ML learned diffuse sensitivity)
    direct_sun = is_sun_in_room_window(
        sun_window_direction,
        window_azimuth,
        avg_learned_az,
        outdoor.solar_azimuth,
        outdoor.solar_elevation,
        outdoor.cloud_cover_ratio,
    )

    if daylight_level == DAYLIGHT_NONE or not outdoor.is_daytime:
        diffuse_modifier = 1.0
    elif direct_sun:
        # Direct sunlight beams create high-contrast glare patches and dark shadows
        # making the rest of the room feel darker relative to the sensor reading
        diffuse_modifier = 0.82
    else:
        # Overcast/diffuse light wraps walls and ceiling evenly (high MRSE)
        diffuse_modifier = outdoor.diffuse_sky_factor * learned_diffuse_sensitivity

    effective_spatial_lux = max(0.0, base_spatial_lux * diffuse_modifier)

    # Subtract artificial light contribution (capped proportionally so natural lux doesn't zero out unrealistically)
    effective_art_lux = min(
        max(0.0, artificial_lux_estimate),
        base_spatial_lux * 0.78,
    )
    natural_raw_est = max(0.0, base_spatial_lux - effective_art_lux)
    natural_spatial_lux = max(0.0, natural_raw_est * diffuse_modifier)

    # 3. Dynamic Comfort Target (E_target)
    # Combines activity baseline + outdoor visual adaptation ("Cave Effect") + circadian + ML weights + user micro-nudges
    base_target = float(activity["base_lux"]) * learned_activity_mult
    if not outdoor.is_daytime:
        base_target *= learned_night_scale
    alpha = float(activity["adaptation_alpha"]) * learned_outdoor_adaptation_scale
    cave_weight = float(activity["cave_contrast_weight"])

    if daylight_level == DAYLIGHT_NONE or outdoor.effective_sky_lux <= 5.0:
        adaptation_offset = 0.0
    else:
        # Logarithmic eye adaptation curve:
        # At 500 lx outdoors -> log10(1 + 5) = 0.78 * alpha
        # At 50,000 lx outdoors -> log10(1 + 500) = 2.70 * alpha
        adaptation_offset = (
            alpha
            * daylight_mult
            * cave_weight
            * math.log10(1.0 + outdoor.effective_sky_lux / 100.0)
        )
        # If direct sun is hitting the window, simultaneous contrast increases the indoor target further
        if direct_sun:
            adaptation_offset *= 1.15

    # Apply contextual feel nudges (weighted between day and night)
    # Each 8% step shifts target_comfort_lux by ~20% (2^(nudge / 32)), allowing full range down to 2 lx!
    active_nudge_pct = (
        feel_nudge_day_pct if outdoor.is_daytime else feel_nudge_night_pct
    )
    nudge_multiplier = math.pow(2.0, active_nudge_pct / 32.0)

    target_comfort_lux = max(
        2.0,
        (base_target * outdoor.circadian_factor + adaptation_offset) * nudge_multiplier,
    )

    # 4. Weber-Fechner Logarithmic Perceptive Score (0 - 100%)
    # Uses log compression with perceptual knee so 0 lux = 0% and target_comfort_lux = 85% ("Comfortable")
    # and 2.5x target_comfort_lux = 100% ("Bright")
    saturation_lux = target_comfort_lux * 2.2
    denom = math.log1p(saturation_lux / 5.0)

    perceptive_pct = clamp(
        100.0 * (math.log1p(effective_spatial_lux / 5.0) / denom),
        0.0,
        100.0,
    )
    natural_perceptive_pct = clamp(
        100.0 * (math.log1p(natural_spatial_lux / 5.0) / denom),
        0.0,
        100.0,
    )

    # Comfort threshold is reached at ~80% perceptive illuminance
    # Lighting deficit is how far below the comfort threshold the natural illuminance sits
    comfort_threshold_pct = 80.0
    if natural_perceptive_pct >= comfort_threshold_pct:
        deficit_pct = 0.0
    else:
        deficit_pct = clamp(
            100.0 * (comfort_threshold_pct - natural_perceptive_pct) / comfort_threshold_pct,
            0.0,
            100.0,
        )

    # Classify Perceptive State (based on total room perceptive illuminance)
    if perceptive_pct >= 82.0:
        state = STATE_BRIGHT
    elif perceptive_pct >= 62.0:
        state = STATE_COMFORTABLE
    elif perceptive_pct >= 38.0:
        state = STATE_DIM
    else:
        state = STATE_DARK

    # 5. Recommended Brightness & Kruithof / Circadian Color Temperature (CCT)
    # Apply Stevens' Power Law / Gamma correction + ML learned brightness scale
    if deficit_pct <= 3.0:
        rec_brightness = 0
    else:
        norm_def = deficit_pct / 100.0
        # Perceptual gamma curve + activity brightness ceiling + ML brightness scale
        scaled = (
            math.pow(norm_def, 0.78)
            * float(activity["brightness_scale"])
            * learned_brightness_scale
        )
        rec_brightness = int(clamp(round(scaled * 100.0), 8, 100))

    # Kruithof Curve + Solar Elevation CCT calculation:
    # Low brightness OR night time -> warm CCT (min_cct)
    # High brightness during daytime -> crisp neutral/daylight CCT (max_cct)
    min_cct = int(activity["min_cct"])
    max_cct = int(activity["max_cct"])
    brightness_factor = (rec_brightness / 100.0) if rec_brightness > 0 else 0.3
    solar_cct_factor = clamp((outdoor.solar_elevation + 4.0) / 45.0, 0.0, 1.0)
    # Kruithof blend: 55% circadian solar elevation + 45% brightness level (never cool at night!)
    kruithof_t = solar_cct_factor * (0.55 + 0.45 * brightness_factor)
    rec_cct = int(round(min_cct + (max_cct - min_cct) * kruithof_t))
    rec_cct = int(clamp(round(rec_cct / 50.0) * 50, 2000, 6500))

    return RoomPerceptiveResult(
        raw_lux=round(raw_lux, 1),
        effective_spatial_lux=round(effective_spatial_lux, 1),
        natural_spatial_lux=round(natural_spatial_lux, 1),
        artificial_lux_contribution=round(artificial_lux_estimate, 1),
        target_comfort_lux=round(target_comfort_lux, 1),
        perceptive_illuminance_pct=round(perceptive_pct, 1),
        natural_perceptive_illuminance_pct=round(natural_perceptive_pct, 1),
        lighting_deficit_pct=round(deficit_pct, 1),
        perceptive_state=state,
        diffuse_modifier=round(diffuse_modifier, 2),
        direct_sun_in_window=direct_sun,
        internal_contrast_ratio=round(internal_contrast, 2),
        outdoor_adaptation_offset_lux=round(adaptation_offset, 1),
        recommended_brightness_pct=rec_brightness,
        recommended_cct_kelvin=rec_cct,
        active_activity=activity_key,
        sensor_breakdown={k: round(v, 1) for k, v in calibrated_readings.items()},
    )
