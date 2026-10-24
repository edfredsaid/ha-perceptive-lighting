"""Closed-Loop Perceptive Illuminance & Smooth Regime Blending Engine (v2.0.0).

Implements:
1. Configurable Time-of-Day Regimes (`morning`, `day`, `evening`, `overnight`) with
   Continuous Smooth Cross-Fading (`regime_weights`) so there are zero step-change cliffs
   before or at sunset/dawn.
2. Closed-Loop Measured `Total Room Lux` Sense-Check Architecture:
   - Decouples Turn-On Lux Threshold (`turn_on_lux`, in real room sensor lux 1..30 lx)
     from Target Light Brightness (`target_brightness_pct`, 1%..100%) and Measured Target
     Total Room Lux (`target_total_lux`).
   - Uses actual measured `Total Room Lux` as the primary reference frame.
3. True `1%` Minimum Brightness Floor & Progressive One-by-One Light Shedding Support.
4. Normalized Competing Mathematical Sub-Models (5 Illuminance Curves x 4 Weather Models x 4 Season/Thermal Models)
   bounded safely (`±25%`) so no model can ever distort thresholds to extreme values.
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
    DEFAULT_REGIME_SCHEDULE,
    DEFAULT_REGIME_SETTINGS,
    ILLUM_MODEL_CIE_DAYLIGHT,
    ILLUM_MODEL_CUTTLE_MRSE,
    ILLUM_MODEL_NAKA_RUSHTON,
    ILLUM_MODEL_STEVENS_POWER,
    ILLUM_MODEL_WEBER_FECHNER,
    PLACEMENT_MULTIPLIERS,
    REGIME_DAY,
    REGIME_EVENING,
    REGIME_MORNING,
    REGIME_OVERNIGHT,
    SEASON_MODEL_BIOCLIMATIC,
    SEASON_MODEL_NEUTRAL,
    SEASON_MODEL_PHOTOPERIOD,
    SEASON_MODEL_THERMAL_HUE_HEAT,
    STATE_BRIGHT,
    STATE_COMFORTABLE,
    STATE_DARK,
    STATE_DIM,
    SUN_DIR_COMPASS,
    SUN_DIR_EVENING,
    SUN_DIR_MIDDAY,
    SUN_DIR_MORNING,
    SUN_DIR_NEVER,
    TIME_REGIMES,
    WEATHER_MODEL_CAVE_CONTRAST,
    WEATHER_MODEL_GLOOM_COMPENSATION,
    WEATHER_MODEL_NEUTRAL,
    WEATHER_MODEL_SOLAR_ALTITUDE,
)


WEATHER_CONDITION_MAP: dict[str, tuple[float, float]] = {
    "clear-night": (0.0, 1.0),
    "sunny": (0.10, 0.94),
    "partlycloudy": (0.45, 1.0),
    "cloudy": (0.85, 1.04),
    "fog": (0.95, 1.05),
    "rainy": (0.90, 1.04),
    "pouring": (0.98, 1.02),
    "snowy": (0.85, 1.08),
    "snowy-rainy": (0.90, 1.05),
    "windy": (0.40, 1.0),
    "windy-variant": (0.60, 1.02),
    "lightning": (0.95, 1.02),
    "lightning-rainy": (0.95, 1.02),
    "hail": (0.95, 1.02),
    "exceptional": (0.50, 1.0),
}

REGIME_BASE_MULTIPLIERS: dict[str, float] = {
    REGIME_MORNING: 0.90,
    REGIME_DAY: 1.00,
    REGIME_EVENING: 1.00,
    REGIME_OVERNIGHT: 0.25,
}


def clamp(val: float, low: float, high: float) -> float:
    """Clamp a value between low and high."""
    return max(low, min(high, val))


def smoothstep(edge0: float, edge1: float, x: float) -> float:
    """Hermite smoothstep interpolation in [0.0, 1.0]."""
    if abs(edge1 - edge0) < 1e-6:
        return 1.0 if x >= edge0 else 0.0
    t = clamp((x - edge0) / (edge1 - edge0), 0.0, 1.0)
    return t * t * (3.0 - 2.0 * t)


def compute_regime_blend(
    solar_elevation: float,
    local_hour: float | None = None,
    weather_condition: str = "cloudy",
    effective_sky_lux: float = 500.0,
    schedule: dict[str, Any] | None = None,
) -> tuple[str, dict[str, float], str]:
    """Compute primary Time-of-Day Regime, continuous blend weights summing to 1.0, and reason string.

    Uses smooth cross-fading across boundaries (`transition_minutes`) so there is never an abrupt
    cliff before or at sunset/dawn.
    """
    cfg = dict(DEFAULT_REGIME_SCHEDULE)
    if isinstance(schedule, dict):
        cfg.update(schedule)

    mode = str(cfg.get("mode", "hybrid"))
    m_hr = float(cfg.get("morning_start_hour", 6.0))
    m_el = float(cfg.get("morning_solar_elev", -4.0))
    d_hr = float(cfg.get("day_start_hour", 10.0))
    d_el = float(cfg.get("day_solar_elev", 14.0))
    e_hr = float(cfg.get("evening_start_hour", 17.5))
    e_el = float(cfg.get("evening_solar_elev", 9.0))
    o_hr = float(cfg.get("overnight_start_hour", 23.0))
    o_end_hr = float(cfg.get("overnight_end_hour", 6.0))
    gloom_early = bool(cfg.get("gloom_early_evening", True))
    gloom_lux_thresh = float(cfg.get("gloom_sky_lux_threshold", 350.0))
    trans_mins = clamp(float(cfg.get("transition_minutes", 45.0)), 5.0, 120.0)

    hr = float(local_hour) if local_hour is not None else 12.0
    fade_hr = clamp(trans_mins / 60.0, 0.1, 2.0)
    fade_el = max(2.0, fade_hr * 7.0)

    # 1. Overnight weight (late night -> early morning)
    if mode == "solar_only":
        w_overnight = 1.0 - smoothstep(m_el - 4.0, m_el + 1.0, solar_elevation)
        if hr >= 12.0 and hr < 22.0:
            w_overnight = 0.0
    else:
        # Late evening ramp into overnight (around o_hr, e.g. 23:00)
        if o_hr <= 24.0:
            late_ramp = smoothstep(o_hr - fade_hr * 0.6, o_hr + fade_hr * 0.4, hr)
        else:
            late_ramp = 0.0
        # Early morning ramp out of overnight (around o_end_hr / m_hr, e.g. 06:00)
        early_stay = 1.0 - smoothstep(o_end_hr - fade_hr * 0.5, o_end_hr + fade_hr * 0.5, hr)
        w_overnight = clamp(max(late_ramp, early_stay), 0.0, 1.0)

    awake_share = max(0.0, 1.0 - w_overnight)

    # 2. Within awake_share, determine Morning vs Day vs Evening smoothly
    if hr < 13.0:
        # Morning -> Day transition
        if mode == "clock_only":
            day_prog = smoothstep(d_hr - fade_hr * 0.5, d_hr + fade_hr * 0.5, hr)
        elif mode == "solar_only":
            day_prog = smoothstep(d_el - fade_el * 0.5, d_el + fade_el * 0.5, solar_elevation)
        else:
            time_prog = smoothstep(d_hr - fade_hr * 0.5, d_hr + fade_hr * 0.5, hr)
            sol_prog = smoothstep(d_el - fade_el * 0.5, d_el + fade_el * 0.5, solar_elevation)
            day_prog = max(time_prog * 0.7 + sol_prog * 0.3, time_prog if hr >= d_hr + 0.5 else 0.0)

        w_morning = awake_share * (1.0 - day_prog)
        w_day = awake_share * day_prog
        w_evening = 0.0
    else:
        # Afternoon / Evening: Day -> Evening transition
        w_morning = 0.0
        if mode == "clock_only":
            eve_prog = smoothstep(e_hr - fade_hr * 0.6, e_hr + fade_hr * 0.4, hr)
        elif mode == "solar_only":
            eve_prog = 1.0 - smoothstep(e_el - fade_el * 0.4, e_el + fade_el * 0.6, solar_elevation)
        else:
            time_eve = smoothstep(e_hr - fade_hr * 0.6, e_hr + fade_hr * 0.4, hr)
            # Start blending into Evening as afternoon sun drops toward e_el (e.g. 9°)
            sol_eve = 1.0 - smoothstep(e_el - fade_el * 0.3, e_el + fade_el * 0.7, solar_elevation)
            eve_prog = max(time_eve, sol_eve)

        # Optional Weather Gloom Early-Evening shift on dark rainy/overcast afternoons
        gloom_boost = 0.0
        if (
            gloom_early
            and hr >= 14.0
            and weather_condition in ("rainy", "pouring", "fog", "cloudy", "lightning-rainy", "snowy-rainy")
            and effective_sky_lux < gloom_lux_thresh
        ):
            gloom_factor = 1.0 - clamp(effective_sky_lux / max(20.0, gloom_lux_thresh), 0.0, 1.0)
            gloom_boost = 0.55 * gloom_factor * smoothstep(13.5, 15.5, hr)
            eve_prog = clamp(max(eve_prog, gloom_boost), 0.0, 1.0)

        w_evening = awake_share * eve_prog
        w_day = awake_share * (1.0 - eve_prog)

    raw_weights = {
        REGIME_MORNING: round(w_morning, 3),
        REGIME_DAY: round(w_day, 3),
        REGIME_EVENING: round(w_evening, 3),
        REGIME_OVERNIGHT: round(w_overnight, 3),
    }
    total_w = sum(raw_weights.values())
    if total_w > 0:
        weights = {k: round(v / total_w, 3) for k, v in raw_weights.items()}
    else:
        weights = {REGIME_MORNING: 0.0, REGIME_DAY: 1.0, REGIME_EVENING: 0.0, REGIME_OVERNIGHT: 0.0}

    primary = max(weights, key=lambda k: weights[k])

    # Build human-readable explanation of active regime & blend
    active_parts = [
        f"{r.title()} {int(round(w * 100))}%"
        for r, w in sorted(weights.items(), key=lambda x: -x[1])
        if w >= 0.05
    ]
    blend_str = " / ".join(active_parts)
    if len(active_parts) > 1:
        reason = (
            f"Smooth transition ({blend_str}) • Clock {int(hr):02d}:{int((hr % 1) * 60):02d}, "
            f"Sun {solar_elevation:.1f}°, Sky {effective_sky_lux:.0f} lx ({weather_condition})"
        )
    else:
        reason = (
            f"{primary.title()} (100%) • Clock {int(hr):02d}:{int((hr % 1) * 60):02d}, "
            f"Sun {solar_elevation:.1f}°, Sky {effective_sky_lux:.0f} lx ({weather_condition})"
        )

    return primary, weights, reason


def determine_time_regime(
    solar_elevation: float,
    local_hour: int | float | None = None,
    schedule: dict[str, Any] | None = None,
) -> str:
    """Return primary time regime string for backward compatibility."""
    primary, _, _ = compute_regime_blend(
        solar_elevation=solar_elevation,
        local_hour=float(local_hour) if local_hour is not None else 12.0,
        schedule=schedule,
    )
    return primary


@dataclass
class SensorLearnedProfile:
    """Learned or AI-calibrated profile for an individual lux sensor."""

    entity_id: str
    placement: str = "auto_learn"
    learned_multiplier: float = 1.0
    peak_solar_azimuth: float | None = None
    direct_sun_ratio: float = 1.0
    samples_analyzed: int = 0
    floor_lux: float = 0.0
    dropout_knee_lux: float = 0.0
    artificial_coupling_ratio: float = 0.0
    base_trust_weight: float = 1.0
    suggest_blacklist: bool = False
    ai_location_guess: str = ""

    def compute_dynamic_weight(
        self,
        raw_lux: float,
        lights_are_on: bool = False,
        direct_sun_in_window: bool = False,
        for_natural_lux: bool = False,
    ) -> float:
        """Compute instantaneous trust weight [0.0, 1.0] for this sensor."""
        if self.suggest_blacklist or self.base_trust_weight <= 0.01:
            return 0.0

        weight = clamp(self.base_trust_weight, 0.0, 1.0)

        if self.dropout_knee_lux > self.floor_lux and raw_lux < self.dropout_knee_lux:
            floor_factor = smoothstep(
                self.floor_lux, self.dropout_knee_lux, raw_lux
            )
            weight *= 0.20 + 0.80 * floor_factor

        if lights_are_on and for_natural_lux and self.artificial_coupling_ratio > 0.15:
            weight *= clamp(1.0 - 0.65 * self.artificial_coupling_ratio, 0.25, 1.0)

        if direct_sun_in_window and self.direct_sun_ratio > 1.6:
            glare_weight = clamp(1.0 / math.sqrt(self.direct_sun_ratio), 0.45, 1.0)
            weight *= glare_weight

        return round(clamp(weight, 0.0, 1.0), 3)

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
            "floor_lux": round(self.floor_lux, 1),
            "dropout_knee_lux": round(self.dropout_knee_lux, 1),
            "artificial_coupling_ratio": round(self.artificial_coupling_ratio, 2),
            "base_trust_weight": round(self.base_trust_weight, 2),
            "suggest_blacklist": self.suggest_blacklist,
            "ai_location_guess": self.ai_location_guess,
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
            floor_lux=float(data.get("floor_lux", 0.0)),
            dropout_knee_lux=float(data.get("dropout_knee_lux", 0.0)),
            artificial_coupling_ratio=float(data.get("artificial_coupling_ratio", 0.0)),
            base_trust_weight=float(data.get("base_trust_weight", 1.0)),
            suggest_blacklist=bool(data.get("suggest_blacklist", False)),
            ai_location_guess=str(data.get("ai_location_guess", "")),
        )


@dataclass
class OutdoorEnvironmentState:
    """Represents the live outdoor lighting, weather, seasonal, solar, and regime blend state."""

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
    sky_quality: str = "diffuse"
    outdoor_temp_c: float | None = None
    day_length_hours: float = 12.0
    time_regime: str = REGIME_DAY
    regime_weights: dict[str, float] = field(
        default_factory=lambda: {
            REGIME_MORNING: 0.0,
            REGIME_DAY: 1.0,
            REGIME_EVENING: 0.0,
            REGIME_OVERNIGHT: 0.0,
        }
    )
    regime_explanation: str = ""


@dataclass
class RoomPerceptiveResult:
    """Complete output of the Closed-Loop Perceptive Lighting calculation for a room."""

    raw_lux: float = 0.0
    effective_spatial_lux: float = 0.0  # Measured Total Room Lux
    natural_spatial_lux: float = 0.0
    artificial_lux_contribution: float = 0.0  # Measured light delta
    min_artificial_lux: float = 2.0
    turn_on_lux_threshold: float = 12.0
    turn_off_lux_threshold: float = 18.0
    target_comfort_lux: float = 14.0  # Target Total Room Lux
    perceptive_illuminance_pct: float = 0.0
    natural_perceptive_illuminance_pct: float = 0.0
    lighting_deficit_pct: float = 100.0
    perceptive_state: str = STATE_DARK
    diffuse_modifier: float = 1.0
    direct_sun_in_window: bool = False
    internal_contrast_ratio: float = 1.0
    outdoor_adaptation_offset_lux: float = 0.0
    recommended_brightness_pct: int = 0
    recommended_shed_count: int = 0
    recommended_cct_kelvin: int = 2700
    active_activity: str = ACTIVITY_RELAXING
    time_regime: str = REGIME_DAY
    regime_weights: dict[str, float] = field(default_factory=dict)
    active_illum_model: str = ILLUM_MODEL_WEBER_FECHNER
    active_weather_model: str = WEATHER_MODEL_CAVE_CONTRAST
    active_season_model: str = SEASON_MODEL_NEUTRAL
    weather_modifier: float = 1.0
    season_thermal_modifier: float = 1.0
    indoor_temp_c: float | None = None
    indoor_humidity_pct: float | None = None
    should_turn_lights_on: bool = False
    should_turn_lights_off_while_occupied: bool = False
    sensor_breakdown: dict[str, float] = field(default_factory=dict)
    sensor_dynamic_weights: dict[str, float] = field(default_factory=dict)


def estimate_synthetic_outdoor_lux(
    solar_elevation: float, cloud_cover_ratio: float
) -> float:
    """Estimate outdoor illuminance (lux) from solar elevation and cloud cover."""
    if solar_elevation <= -6.0:
        return 0.1
    if solar_elevation <= 0.0:
        t = (solar_elevation + 6.0) / 6.0
        base_twilight = 1.0 * math.pow(400.0, t)
        return base_twilight * (1.0 - 0.55 * cloud_cover_ratio)

    sin_el = math.sin(math.radians(clamp(solar_elevation, 0.5, 90.0)))
    clear_sky_lux = 95000.0 * math.pow(sin_el, 1.35) + 400.0
    cloud_attenuation = 1.0 - 0.80 * math.pow(clamp(cloud_cover_ratio, 0.0, 1.0), 1.3)
    return max(10.0, clear_sky_lux * cloud_attenuation)


def estimate_day_length_hours(latitude: float, day_of_year: int) -> float:
    """Estimate astronomical photoperiod (daylight hours) from latitude and day of year (1..366)."""
    decl = math.radians(23.44 * math.sin(math.radians((360.0 / 365.0) * (day_of_year - 81))))
    lat_rad = math.radians(clamp(latitude, -65.0, 65.0))
    cos_omega = clamp(-math.tan(lat_rad) * math.tan(decl), -0.98, 0.98)
    omega_deg = math.degrees(math.acos(cos_omega))
    return round(clamp((2.0 * omega_deg) / 15.0, 4.0, 20.0), 2)


def compute_outdoor_environment(
    sensor_readings: dict[str, float],
    sensor_profiles: dict[str, SensorLearnedProfile],
    weather_condition: str | None,
    weather_cloud_coverage: float | None,
    solar_elevation: float,
    solar_azimuth: float,
    outdoor_temp_c: float | None = None,
    day_length_hours: float = 12.0,
    local_hour: int | float | None = None,
    regime_schedule: dict[str, Any] | None = None,
) -> OutdoorEnvironmentState:
    """Compute unified outdoor illuminance, weather/solar context, and smooth Time-of-Day Regime blend."""
    cond = (weather_condition or "cloudy").lower()
    est_clouds, base_diffuse = WEATHER_CONDITION_MAP.get(cond, (0.5, 1.0))

    if weather_cloud_coverage is not None:
        cloud_ratio = clamp(float(weather_cloud_coverage) / 100.0, 0.0, 1.0)
        diffuse_factor = 0.92 + 0.16 * math.sin(cloud_ratio * math.pi * 0.75)
        diffuse_factor = (diffuse_factor + base_diffuse) / 2.0
    else:
        cloud_ratio = est_clouds
        diffuse_factor = base_diffuse

    synthetic_lux = estimate_synthetic_outdoor_lux(solar_elevation, cloud_ratio)

    valid_readings: dict[str, float] = {}
    for eid, val in sensor_readings.items():
        prof = sensor_profiles.get(eid)
        if prof and (prof.suggest_blacklist or prof.base_trust_weight <= 0.05):
            continue
        if solar_elevation > 15.0 and val <= 1.0 and synthetic_lux > 3000.0:
            continue
        valid_readings[eid] = val

    if not valid_readings:
        raw_lux = synthetic_lux
        effective_lux = synthetic_lux
        is_synthetic = True
    else:
        is_synthetic = False
        values = list(valid_readings.values())
        raw_lux = sum(values) / len(values)
        effective_lux = max(values) if len(values) > 1 else values[0]

    time_regime, regime_weights, regime_explanation = compute_regime_blend(
        solar_elevation=solar_elevation,
        local_hour=float(local_hour) if local_hour is not None else 12.0,
        weather_condition=cond,
        effective_sky_lux=effective_lux,
        schedule=regime_schedule,
    )

    if solar_elevation <= -6.0:
        circadian = 0.65
        sky_quality = "night"
    elif solar_elevation <= 2.0:
        circadian = 0.75 + 0.15 * ((solar_elevation + 6.0) / 8.0)
        sky_quality = "twilight"
    else:
        el_norm = clamp(solar_elevation / 55.0, 0.0, 1.0)
        circadian = 0.90 + 0.15 * math.pow(el_norm, 0.7)
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
        outdoor_temp_c=round(outdoor_temp_c, 1) if outdoor_temp_c is not None else None,
        day_length_hours=round(day_length_hours, 2),
        time_regime=time_regime,
        regime_weights=regime_weights,
        regime_explanation=regime_explanation,
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


def compute_weather_model_modifier(
    weather_model: str,
    outdoor: OutdoorEnvironmentState,
    daylight_level: str,
    direct_sun: bool,
) -> float:
    """Stage 2 Competing Weather Models (W1..W4): bounded cleanly in [0.80, 1.25]."""
    if daylight_level == DAYLIGHT_NONE or outdoor.solar_elevation <= -2.0:
        return 1.0

    # Fade weather modifier smoothly across civil twilight (-2° .. +6°) so there is no sunset jump
    day_fade = smoothstep(-2.0, 6.0, outdoor.solar_elevation)
    daylight_mult = DAYLIGHT_ADAPTATION_MULTIPLIERS.get(daylight_level, 1.0)
    clouds = outdoor.cloud_cover_ratio
    cond = outdoor.weather_condition

    raw_mod = 1.0
    if weather_model == WEATHER_MODEL_CAVE_CONTRAST:
        sky_factor = clamp(math.log10(1.0 + outdoor.effective_sky_lux / 1000.0) / 2.0, 0.0, 1.2)
        raw_mod = 1.0 + 0.18 * daylight_mult * (sky_factor - 0.45 * clouds)
        if direct_sun:
            raw_mod *= 1.08
    elif weather_model == WEATHER_MODEL_GLOOM_COMPENSATION:
        is_rainy = cond in ("rainy", "pouring", "fog", "lightning-rainy", "snowy-rainy")
        gloom = (clouds - 0.45) * 0.35 + (0.10 if is_rainy else 0.0)
        if direct_sun and clouds < 0.35:
            gloom -= 0.15
        raw_mod = 1.0 + gloom
    elif weather_model == WEATHER_MODEL_SOLAR_ALTITUDE:
        el = outdoor.solar_elevation
        if el < 25.0:
            penetration_relief = -0.12 * (1.0 - clouds * 0.5)
        else:
            penetration_relief = 0.14 * ((el - 25.0) / 40.0)
        raw_mod = 1.0 + penetration_relief

    blended_mod = 1.0 + day_fade * (raw_mod - 1.0)
    return round(clamp(blended_mod, 0.80, 1.25), 3)


def compute_season_thermal_modifier(
    season_model: str,
    outdoor: OutdoorEnvironmentState,
    indoor_temp_c: float | None = None,
    indoor_humidity_pct: float | None = None,
) -> float:
    """Stage 3 Competing Seasonal, Photoperiod & Thermal Models (S1..S4): bounded in [0.85, 1.20]."""
    if season_model == SEASON_MODEL_NEUTRAL:
        return 1.0

    day_len = outdoor.day_length_hours
    photoperiod_delta = clamp((12.0 - day_len) / 8.0, -0.5, 0.5)
    awake_weight = 1.0 - outdoor.regime_weights.get(REGIME_OVERNIGHT, 0.0)
    photo_mult = 1.0 + 0.16 * photoperiod_delta * awake_weight

    temp_delta = 0.0
    if outdoor.outdoor_temp_c is not None:
        temp_delta += clamp((15.0 - outdoor.outdoor_temp_c) / 14.0, -0.6, 0.6) * 0.60
    if indoor_temp_c is not None:
        temp_delta += clamp((21.0 - indoor_temp_c) / 5.0, -0.6, 0.6) * 0.40
    if indoor_humidity_pct is not None and indoor_humidity_pct > 68.0 and temp_delta < 0:
        temp_delta -= 0.05

    thermal_mult = clamp(1.0 + 0.16 * temp_delta, 0.85, 1.20)

    if season_model == SEASON_MODEL_PHOTOPERIOD:
        return round(clamp(photo_mult, 0.85, 1.20), 3)
    if season_model == SEASON_MODEL_THERMAL_HUE_HEAT:
        return round(clamp(thermal_mult, 0.85, 1.20), 3)
    if season_model == SEASON_MODEL_BIOCLIMATIC:
        return round(clamp(0.55 * photo_mult + 0.45 * thermal_mult, 0.85, 1.20), 3)

    return 1.0


def evaluate_normalized_perceptive_curve(
    illum_model: str,
    lux_val: float,
    target_lux: float,
    internal_contrast: float = 1.0,
    solar_elevation: float = 15.0,
) -> float:
    """Evaluate normalized perceptual illuminance percentage [0..100%] where `lux_val == target_lux` -> `80.0%`."""
    if lux_val <= 0.0:
        return 0.0
    t_lux = max(1.0, target_lux)

    if illum_model == ILLUM_MODEL_STEVENS_POWER:
        # Exponent 0.55 gives a realistic low-lux response so 4 lx vs 14 lx is properly recognized as dark/dim
        beta = 0.58 if solar_elevation > 0.0 else 0.52
        pct = 80.0 * math.pow(lux_val / t_lux, beta)
        return clamp(pct, 0.0, 100.0)

    if illum_model == ILLUM_MODEL_NAKA_RUSHTON:
        n_exp = 1.35
        sigma = max(0.5, t_lux / math.pow(4.0, 1.0 / n_exp))
        en = math.pow(lux_val, n_exp)
        sn = math.pow(sigma, n_exp)
        return clamp(100.0 * (en / (en + sn)), 0.0, 100.0)

    if illum_model == ILLUM_MODEL_CUTTLE_MRSE:
        contrast_penalty = clamp(1.0 / math.pow(max(1.0, internal_contrast), 0.20), 0.70, 1.0)
        eff_val = lux_val * contrast_penalty
    elif illum_model == ILLUM_MODEL_CIE_DAYLIGHT:
        if solar_elevation > 4.0:
            sin_el = math.sin(math.radians(clamp(solar_elevation, 5.0, 85.0)))
            eff_val = lux_val * clamp(1.12 - 0.22 * sin_el, 0.90, 1.12)
        else:
            eff_val = lux_val
    else:
        # ILLUM_MODEL_WEBER_FECHNER
        eff_val = lux_val

    # Anchor log curve so eff_val == t_lux ALWAYS maps to 80.0%!
    knee = max(0.8, t_lux * 0.25)
    denom = max(1e-4, math.log1p(t_lux / knee))
    pct = 80.0 * (math.log1p(max(0.0, eff_val) / knee) / denom)
    return clamp(pct, 0.0, 100.0)


def compute_base_illuminance_model(
    illum_model: str,
    effective_spatial_lux: float,
    natural_spatial_lux: float,
    base_activity_lux: float,
    adaptation_alpha: float,
    cave_weight: float,
    daylight_mult: float,
    outdoor: OutdoorEnvironmentState,
    internal_contrast: float,
    weather_mod: float,
    season_mod: float,
    regime_mult: float,
    nudge_multiplier: float,
) -> tuple[float, float, float]:
    """Evaluate target lux and normalized perceptual percentages (backward-compatible signature)."""
    target_lux = max(1.0, base_activity_lux * regime_mult * weather_mod * season_mod * nudge_multiplier)
    total_pct = evaluate_normalized_perceptive_curve(
        illum_model, effective_spatial_lux, target_lux, internal_contrast, outdoor.solar_elevation
    )
    nat_pct = evaluate_normalized_perceptive_curve(
        illum_model, natural_spatial_lux, target_lux, internal_contrast, outdoor.solar_elevation
    )
    return target_lux, total_pct, nat_pct


def compute_room_perceptive_illuminance(
    indoor_sensor_readings: dict[str, float],
    sensor_profiles: dict[str, SensorLearnedProfile],
    outdoor: OutdoorEnvironmentState,
    activity_key: str = ACTIVITY_RELAXING,
    daylight_level: str = DAYLIGHT_MEDIUM,
    sun_window_direction: str = "auto_learn",
    window_azimuth: float | None = None,
    artificial_lux_estimate: float = 0.0,
    min_artificial_lux: float = 2.0,
    lights_are_on: bool = False,
    feel_nudge_day_pct: float = 0.0,
    feel_nudge_night_pct: float = 0.0,
    learned_activity_mult: float = 1.0,
    learned_outdoor_adaptation_scale: float = 1.0,
    learned_diffuse_sensitivity: float = 1.0,
    learned_night_scale: float = 1.0,
    learned_brightness_scale: float = 1.0,
    illum_model: str = ILLUM_MODEL_WEBER_FECHNER,
    weather_model: str = WEATHER_MODEL_CAVE_CONTRAST,
    season_model: str = SEASON_MODEL_NEUTRAL,
    regime_target_scale: float = 1.0,
    indoor_temp_c: float | None = None,
    indoor_humidity_pct: float | None = None,
    regime_configs: dict[str, dict[str, Any]] | None = None,
) -> RoomPerceptiveResult:
    """Compute Closed-Loop Perceptive Illuminance using Measured Total Room Lux & Smooth Regime Blending."""
    activity = ACTIVITY_DEFINITIONS.get(activity_key, ACTIVITY_DEFINITIONS[ACTIVITY_RELAXING])
    regime = outdoor.time_regime
    r_weights = outdoor.regime_weights or {regime: 1.0}

    # 1. Determine solar window beam status
    learned_azimuths = [
        p.peak_solar_azimuth
        for p in sensor_profiles.values()
        if p.peak_solar_azimuth is not None and not p.suggest_blacklist
    ]
    avg_learned_az = (
        sum(learned_azimuths) / len(learned_azimuths) if learned_azimuths else None
    )
    direct_sun = is_sun_in_room_window(
        sun_window_direction,
        window_azimuth,
        avg_learned_az,
        outdoor.solar_azimuth,
        outdoor.solar_elevation,
        outdoor.cloud_cover_ratio,
    )

    # 2. Measure Total Room Lux from active indoor sensors (excluding blacklisted/dead sensors)
    calibrated_readings: dict[str, float] = {}
    dynamic_weights: dict[str, float] = {}

    for eid, raw_val in indoor_sensor_readings.items():
        prof = sensor_profiles.get(eid)
        placement = prof.placement if prof else "balanced"
        mult = PLACEMENT_MULTIPLIERS.get(placement, 1.0)
        if prof and placement == "auto_learn":
            mult = prof.learned_multiplier

        cal_val = max(0.0, raw_val * mult)
        calibrated_readings[eid] = cal_val

        w_total = (
            prof.compute_dynamic_weight(
                raw_val,
                lights_are_on=lights_are_on,
                direct_sun_in_window=direct_sun,
                for_natural_lux=False,
            )
            if prof
            else 1.0
        )
        dynamic_weights[eid] = w_total

    if calibrated_readings:
        # Exclude blacklisted (weight == 0) sensors from raw_lux too so a dead 0 lx sensor never drags raw_lux down!
        active_raw_vals = [
            indoor_sensor_readings[k]
            for k, w in dynamic_weights.items()
            if w > 0.01
        ] or list(indoor_sensor_readings.values())
        raw_lux = sum(active_raw_vals) / len(active_raw_vals)

        total_w_sum = sum(dynamic_weights.values())
        if total_w_sum > 0.01:
            total_room_lux = (
                sum(calibrated_readings[k] * dynamic_weights[k] for k in calibrated_readings)
                / total_w_sum
            )
        else:
            total_room_lux = raw_lux

        trusted_vals = [
            calibrated_readings[k]
            for k, w in dynamic_weights.items()
            if w >= 0.25
        ] or list(calibrated_readings.values())
        if len(trusted_vals) >= 2:
            min_v = max(1.0, min(trusted_vals))
            max_v = max(1.0, max(trusted_vals))
            internal_contrast = max_v / min_v
        else:
            internal_contrast = 1.0
    else:
        raw_lux = 0.0
        total_room_lux = 0.0
        internal_contrast = 1.0

    # Keep effective_spatial_lux equal to the real measured total_room_lux (no artificial 1.4x inflation!)
    effective_spatial_lux = max(0.0, total_room_lux)

    # Measured light delta (only subtracted when lights are actually ON to estimate underlying ambient lux)
    measured_light_delta = (
        clamp(artificial_lux_estimate, 0.0, max(1.0, effective_spatial_lux * 0.70))
        if lights_are_on
        else 0.0
    )
    natural_spatial_lux = max(0.0, effective_spatial_lux - measured_light_delta)

    # 3. Blend per-regime settings smoothly across `regime_weights` (Zero Sunset Cliffs!)
    rcfg_map = regime_configs or DEFAULT_REGIME_SETTINGS
    blended_turn_on_lux = 0.0
    blended_target_bri = 0.0
    blended_target_total_lux = 0.0
    blended_shed_count = 0.0

    for reg_key in TIME_REGIMES:
        w = float(r_weights.get(reg_key, 0.0))
        if w <= 0.0:
            continue
        rset = rcfg_map.get(reg_key, DEFAULT_REGIME_SETTINGS[reg_key])
        blended_turn_on_lux += w * float(rset.get("turn_on_lux", DEFAULT_REGIME_SETTINGS[reg_key]["turn_on_lux"]))
        blended_target_bri += w * float(rset.get("target_brightness_pct", DEFAULT_REGIME_SETTINGS[reg_key]["target_brightness_pct"]))
        blended_target_total_lux += w * float(rset.get("target_total_lux", DEFAULT_REGIME_SETTINGS[reg_key]["target_total_lux"]))
        blended_shed_count += w * float(rset.get("shed_count", 0))

    # Apply bounded Weather and Season/Thermal modifiers to Turn-On Lux Threshold
    weather_mod = compute_weather_model_modifier(
        weather_model, outdoor, daylight_level, direct_sun
    )
    season_mod = compute_season_thermal_modifier(
        season_model, outdoor, indoor_temp_c, indoor_humidity_pct
    )

    turn_on_lux = max(
        0.5,
        blended_turn_on_lux * weather_mod * season_mod * clamp(regime_target_scale, 0.50, 1.80),
    )
    target_comfort_lux = max(
        turn_on_lux + 1.5,
        blended_target_total_lux * weather_mod * season_mod,
    )

    # Turn-off threshold while occupied uses Total Room Lux directly as the sense check!
    # Lights only turn OFF while occupied when Total Room Lux rises well above (turn_on_lux + what the lights add)
    # AND above 125% of target_comfort_lux.
    turn_off_lux = max(
        turn_on_lux + measured_light_delta + 2.5,
        target_comfort_lux * 1.25,
    )

    # 4. Evaluate Normalized Perceptive Illuminance Percentages (80% == Comfort Target)
    perceptive_pct = evaluate_normalized_perceptive_curve(
        illum_model,
        effective_spatial_lux,
        target_comfort_lux,
        internal_contrast,
        outdoor.solar_elevation,
    )
    natural_perceptive_pct = evaluate_normalized_perceptive_curve(
        illum_model,
        natural_spatial_lux,
        turn_on_lux,
        internal_contrast,
        outdoor.solar_elevation,
    )

    if natural_perceptive_pct >= 80.0:
        deficit_pct = 0.0
    else:
        deficit_pct = clamp(100.0 * (80.0 - natural_perceptive_pct) / 80.0, 0.0, 100.0)

    # 5. On/Off Decision Rules (Grounded directly in Total Room Lux!)
    if not lights_are_on:
        # Lights are currently OFF: turn ON as soon as Total Room Lux is below turn_on_lux
        should_turn_on = effective_spatial_lux < turn_on_lux
        should_turn_off_occupied = effective_spatial_lux >= turn_on_lux
    else:
        # Lights are currently ON: keep them on unless Total Room Lux exceeds turn_off_lux
        should_turn_off_occupied = effective_spatial_lux >= turn_off_lux
        should_turn_on = not should_turn_off_occupied

    # Classify Perceptive State
    if effective_spatial_lux >= turn_off_lux or perceptive_pct >= 85.0:
        state = STATE_BRIGHT
    elif effective_spatial_lux >= turn_on_lux or perceptive_pct >= 68.0:
        state = STATE_COMFORTABLE
    elif effective_spatial_lux >= turn_on_lux * 0.55 or perceptive_pct >= 38.0:
        state = STATE_DIM
    else:
        state = STATE_DARK

    # 6. Recommended Brightness (`1%` to `100%`) — Decoupled from textbook lux deficit!
    base_target_bri = clamp(blended_target_bri, 1.0, 100.0)
    rec_shed_count = int(round(max(0.0, blended_shed_count)))

    if should_turn_off_occupied and not should_turn_on:
        rec_brightness = 0
    else:
        # When natural_spatial_lux is 0..1 lx (dark), output full `base_target_bri` (e.g. 70%, or 1% if user set 1%!).
        # When natural_spatial_lux is partway toward `turn_on_lux`, smoothly interpolate between
        # a gentle minimum fill (`min(base_target_bri, 25%)`) and `base_target_bri`.
        if turn_on_lux > 1.5 and natural_spatial_lux > 1.0:
            daylight_fill_ratio = clamp(
                (natural_spatial_lux - 1.0) / max(1.0, turn_on_lux - 1.0),
                0.0,
                1.0,
            )
            min_fill_bri = min(base_target_bri, 25.0)
            interpolated_bri = base_target_bri - (base_target_bri - min_fill_bri) * (
                0.65 * daylight_fill_ratio
            )
        else:
            interpolated_bri = base_target_bri

        rec_brightness = int(clamp(round(interpolated_bri), 1, 100))

    # 7. Kruithof / Circadian Color Temperature (CCT)
    min_cct = int(activity["min_cct"])
    max_cct = int(activity["max_cct"])
    brightness_factor = (rec_brightness / 100.0) if rec_brightness > 0 else 0.3
    solar_cct_factor = clamp((outdoor.solar_elevation + 4.0) / 45.0, 0.0, 1.0)
    kruithof_t = solar_cct_factor * (0.55 + 0.45 * brightness_factor)
    rec_cct = int(round(min_cct + (max_cct - min_cct) * kruithof_t))
    rec_cct = int(clamp(round(rec_cct / 50.0) * 50, 2000, 6500))

    return RoomPerceptiveResult(
        raw_lux=round(raw_lux, 1),
        effective_spatial_lux=round(effective_spatial_lux, 1),
        natural_spatial_lux=round(natural_spatial_lux, 1),
        artificial_lux_contribution=round(measured_light_delta, 1),
        min_artificial_lux=round(min_artificial_lux, 1),
        turn_on_lux_threshold=round(turn_on_lux, 1),
        turn_off_lux_threshold=round(turn_off_lux, 1),
        target_comfort_lux=round(target_comfort_lux, 1),
        perceptive_illuminance_pct=round(perceptive_pct, 1),
        natural_perceptive_illuminance_pct=round(natural_perceptive_pct, 1),
        lighting_deficit_pct=round(deficit_pct, 1),
        perceptive_state=state,
        diffuse_modifier=1.0,
        direct_sun_in_window=direct_sun,
        internal_contrast_ratio=round(internal_contrast, 2),
        outdoor_adaptation_offset_lux=round(max(0.0, turn_on_lux - blended_turn_on_lux), 1),
        recommended_brightness_pct=rec_brightness,
        recommended_shed_count=rec_shed_count,
        recommended_cct_kelvin=rec_cct,
        active_activity=activity_key,
        time_regime=regime,
        regime_weights=dict(r_weights),
        active_illum_model=illum_model,
        active_weather_model=weather_model,
        active_season_model=season_model,
        weather_modifier=round(weather_mod, 3),
        season_thermal_modifier=round(season_mod, 3),
        indoor_temp_c=round(indoor_temp_c, 1) if indoor_temp_c is not None else None,
        indoor_humidity_pct=round(indoor_humidity_pct, 1) if indoor_humidity_pct is not None else None,
        should_turn_lights_on=should_turn_on,
        should_turn_lights_off_while_occupied=should_turn_off_occupied,
        sensor_breakdown={k: round(v, 1) for k, v in calibrated_readings.items()},
        sensor_dynamic_weights=dynamic_weights,
    )
