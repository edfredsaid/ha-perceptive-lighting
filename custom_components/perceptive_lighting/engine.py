"""Psychophysical & Architectural Perceptive Illuminance Engine (v1.3.0).

Implements:
1. Five Competing Base Illuminance & Retinal Adaptation Models (I1..I5):
   - Weber-Fechner Logarithmic Adaptation
   - Stevens' Power Law Brightness Scaling
   - Naka-Rushton Retinal Sigmoid (Michaelis-Menten Photoreceptor Saturation)
   - CIE Daylight Factor & Solar Altitude Penetration
   - Kit Cuttle's Mean Room Surface Exitance (MRSE Diffuse Flux Balance)
2. Four Competing Weather & Sky Contrast Models (W1..W4):
   - Simultaneous Window Contrast ("Cave Effect")
   - Overcast Gloom Compensation / Sunny Afternoon Relief
   - Perez Sky & Solar Altitude Penetration
   - Weather-Neutral Control
3. Four Competing Seasonal, Photoperiod & Thermal Models (S1..S4):
   - Photoperiod / Winter SAD Affective Compensation
   - Thermal-Luminance / Hue-Heat Hypothesis (Outdoor & TRV-excluded Indoor Temp)
   - Combined Bioclimatic Comfort Index
   - Season/Temperature-Neutral Control
4. Four Distinct Time-of-Day Regimes (Morning, Day, Evening, Overnight)
5. Dynamic Regime-Dependent Sensor Weighting (High Dark-Floor Drop-Out Tapering,
   Lamp-Coupling Suppression, Solar Glare Damping, and Flatline Outlier Detection)
6. Minimum Artificial Light Floor (Delta E_min) & Occupied Turn-On / Turn-Off Boundaries
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
    WEATHER_MODEL_CAVE_CONTRAST,
    WEATHER_MODEL_GLOOM_COMPENSATION,
    WEATHER_MODEL_NEUTRAL,
    WEATHER_MODEL_SOLAR_ALTITUDE,
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

REGIME_BASE_MULTIPLIERS: dict[str, float] = {
    REGIME_MORNING: 0.92,
    REGIME_DAY: 1.00,
    REGIME_EVENING: 0.78,
    REGIME_OVERNIGHT: 0.22,
}


def clamp(val: float, low: float, high: float) -> float:
    """Clamp a value between low and high."""
    return max(low, min(high, val))


def smoothstep(edge0: float, edge1: float, x: float) -> float:
    """Hermite smoothstep interpolation in [0.0, 1.0] for sensor floor tapering."""
    if edge1 <= edge0:
        return 1.0 if x >= edge0 else 0.0
    t = clamp((x - edge0) / (edge1 - edge0), 0.0, 1.0)
    return t * t * (3.0 - 2.0 * t)


def determine_time_regime(
    solar_elevation: float, local_hour: int | float | None = None
) -> str:
    """Classify current diurnal state into Morning, Day, Evening, or Overnight."""
    hr = float(local_hour) if local_hour is not None else 12.0
    if hr >= 23.0 or hr < 5.0:
        return REGIME_OVERNIGHT
    if solar_elevation <= -6.0 and hr < 10.0:
        return REGIME_OVERNIGHT
    if hr < 10.5 and solar_elevation < 28.0:
        return REGIME_MORNING
    if solar_elevation >= 6.0 and hr < 19.5:
        return REGIME_DAY
    return REGIME_EVENING


@dataclass
class SensorLearnedProfile:
    """Learned or AI-calibrated profile for an individual lux sensor, including dynamic regime weighting."""

    entity_id: str
    placement: str = "auto_learn"
    learned_multiplier: float = 1.0
    peak_solar_azimuth: float | None = None  # Azimuth (0-360) when direct sun hits sensor
    direct_sun_ratio: float = 1.0  # Ratio of sunny-to-cloudy lux at peak azimuth
    samples_analyzed: int = 0
    # Dynamic weighting & outlier characteristics
    floor_lux: float = 0.0  # Lux value where sensor bottoms out / drops to 0 early
    dropout_knee_lux: float = 0.0  # Lux threshold below which sensor weight tapers down
    artificial_coupling_ratio: float = 0.0  # 0..1: how strongly room lamps blind this sensor
    base_trust_weight: float = 1.0  # 0.0 if flatlined/stuck, up to 1.0 for healthy sensor
    suggest_blacklist: bool = False
    ai_location_guess: str = ""

    def compute_dynamic_weight(
        self,
        raw_lux: float,
        lights_are_on: bool = False,
        direct_sun_in_window: bool = False,
        for_natural_lux: bool = False,
    ) -> float:
        """Compute instantaneous trust weight [0.0, 1.0] for this sensor in the current lighting regime."""
        if self.suggest_blacklist or self.base_trust_weight <= 0.01:
            return 0.0

        weight = clamp(self.base_trust_weight, 0.0, 1.0)

        # 1. High-floor / early zero-dropout tapering:
        #    When raw_lux drops below dropout_knee_lux toward floor_lux, smoothly taper weight
        #    so a sensor that drops to 0 lx early at dusk doesn't pull the room average to 0
        if self.dropout_knee_lux > self.floor_lux and raw_lux < self.dropout_knee_lux:
            floor_factor = smoothstep(
                self.floor_lux, self.dropout_knee_lux, raw_lux
            )
            # Keep a tiny 0.05 floor unless completely at/below floor_lux
            weight *= 0.05 + 0.95 * floor_factor

        # 2. Lamp-proximity / artificial light coupling suppression:
        #    If this sensor sits right under a lamp, reduce its weight when estimating natural daylight while lights are ON
        if lights_are_on and for_natural_lux and self.artificial_coupling_ratio > 0.15:
            weight *= clamp(1.0 - 0.75 * self.artificial_coupling_ratio, 0.15, 1.0)

        # 3. Direct window sunbeam spike damping:
        if direct_sun_in_window and self.direct_sun_ratio > 1.6:
            glare_weight = clamp(1.0 / math.sqrt(self.direct_sun_ratio), 0.35, 1.0)
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
    """Represents the live outdoor lighting, weather, seasonal, and solar state."""

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
    outdoor_temp_c: float | None = None
    day_length_hours: float = 12.0
    time_regime: str = REGIME_DAY


@dataclass
class RoomPerceptiveResult:
    """Complete output of the Perceptive Lighting calculation for a room."""

    raw_lux: float = 0.0
    effective_spatial_lux: float = 0.0
    natural_spatial_lux: float = 0.0
    artificial_lux_contribution: float = 0.0
    min_artificial_lux: float = 12.0
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
    time_regime: str = REGIME_DAY
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
    # Solar declination angle (radians)
    decl = math.radians(23.44 * math.sin(math.radians((360.0 / 365.0) * (day_of_year - 81))))
    lat_rad = math.radians(clamp(latitude, -65.0, 65.0))
    cos_omega = -math.tan(lat_rad) * math.tan(decl)
    cos_omega = clamp(cos_omega, -0.98, 0.98)
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
) -> OutdoorEnvironmentState:
    """Compute unified outdoor illuminance, accounting for direct sun vs shade and outlier outdoor sensors."""
    cond = (weather_condition or "cloudy").lower()
    est_clouds, base_diffuse = WEATHER_CONDITION_MAP.get(cond, (0.5, 1.0))

    if weather_cloud_coverage is not None:
        cloud_ratio = clamp(float(weather_cloud_coverage) / 100.0, 0.0, 1.0)
        diffuse_factor = 0.85 + 0.35 * math.sin(cloud_ratio * math.pi * 0.75)
        diffuse_factor = (diffuse_factor + base_diffuse) / 2.0
    else:
        cloud_ratio = est_clouds
        diffuse_factor = base_diffuse

    synthetic_lux = estimate_synthetic_outdoor_lux(solar_elevation, cloud_ratio)
    time_regime = determine_time_regime(solar_elevation, local_hour)

    # Filter out blacklisted or 0-trust outdoor sensors
    valid_readings: dict[str, float] = {}
    for eid, val in sensor_readings.items():
        prof = sensor_profiles.get(eid)
        if prof and (prof.suggest_blacklist or prof.base_trust_weight <= 0.05):
            continue
        # If it's bright midday (>15° sun) and an outdoor sensor reads 0.0 while synthetic is >5000 lx, ignore stuck sensor
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

        corrected_values: list[float] = []
        for eid, val in valid_readings.items():
            prof = sensor_profiles.get(eid)
            if (
                prof
                and prof.peak_solar_azimuth is not None
                and solar_elevation > 8.0
                and cloud_ratio < 0.45
            ):
                az_diff = abs((solar_azimuth - prof.peak_solar_azimuth + 180) % 360 - 180)
                if az_diff < 45.0:
                    glare_damp = clamp(1.0 / max(1.0, math.sqrt(prof.direct_sun_ratio)), 0.55, 1.0)
                    corrected_values.append(val * glare_damp)
                elif az_diff > 110.0 and val < synthetic_lux * 0.35:
                    corrected_values.append(val * 0.65 + synthetic_lux * 0.35)
                else:
                    corrected_values.append(val)
            else:
                corrected_values.append(val)

        effective_lux = max(corrected_values) if len(corrected_values) > 1 else corrected_values[0]

    if solar_elevation <= -6.0:
        circadian = 0.55
        sky_quality = "night"
    elif solar_elevation <= 2.0:
        circadian = 0.65 + 0.20 * ((solar_elevation + 6.0) / 8.0)
        sky_quality = "twilight"
    else:
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
        outdoor_temp_c=round(outdoor_temp_c, 1) if outdoor_temp_c is not None else None,
        day_length_hours=round(day_length_hours, 2),
        time_regime=time_regime,
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


# ============================================================================
# Factored Competing Mathematical Sub-Models (Stage 1: I, Stage 2: W, Stage 3: S)
# ============================================================================


def compute_weather_model_modifier(
    weather_model: str,
    outdoor: OutdoorEnvironmentState,
    daylight_level: str,
    direct_sun: bool,
) -> float:
    """Stage 2 Competing Weather Models (W1..W4): returns target lux multiplier."""
    if daylight_level == DAYLIGHT_NONE or not outdoor.is_daytime:
        return 1.0

    daylight_mult = DAYLIGHT_ADAPTATION_MULTIPLIERS.get(daylight_level, 1.0)
    clouds = outdoor.cloud_cover_ratio
    cond = outdoor.weather_condition

    if weather_model == WEATHER_MODEL_CAVE_CONTRAST:
        # W1: Bright sunny skies raise indoor target due to window simultaneous contrast ("Cave Effect")
        sky_factor = clamp(math.log10(1.0 + outdoor.effective_sky_lux / 1000.0) / 1.8, 0.0, 1.4)
        mod = 1.0 + 0.25 * daylight_mult * (sky_factor - 0.5 * clouds)
        if direct_sun:
            mod *= 1.15
        return clamp(mod, 0.75, 1.65)

    if weather_model == WEATHER_MODEL_GLOOM_COMPENSATION:
        # W2: Psychological Gloom Boost / Sunny Afternoon Relief:
        # Overcast/rainy/dreary days boost target (+15% to +35%) so warm lights come on sooner;
        # Clear sunny days reduce target (-20% to -35%) so lights stay OFF on sunny afternoons!
        is_rainy = cond in ("rainy", "pouring", "fog", "lightning-rainy", "snowy-rainy")
        gloom = (clouds - 0.45) * 0.55 + (0.15 if is_rainy else 0.0)
        if direct_sun and clouds < 0.35:
            gloom -= 0.22
        return clamp(1.0 + gloom, 0.60, 1.45)

    if weather_model == WEATHER_MODEL_SOLAR_ALTITUDE:
        # W3: Perez Sky & Solar Altitude Horizontal Penetration:
        # Low-angle sun (8°..26°) penetrates horizontally deep into the room -> lower target needed (-20%)
        # High summer sun (>50°) only hits the windowsill -> deeper interior needs more fill (+18%)
        el = outdoor.solar_elevation
        if el < 28.0:
            penetration_relief = -0.18 * (1.0 - clouds * 0.5)
        else:
            penetration_relief = 0.20 * ((el - 28.0) / 40.0)
        return clamp(1.0 + penetration_relief, 0.72, 1.35)

    # W4: WEATHER_MODEL_NEUTRAL
    return 1.0


def compute_season_thermal_modifier(
    season_model: str,
    outdoor: OutdoorEnvironmentState,
    indoor_temp_c: float | None = None,
    indoor_humidity_pct: float | None = None,
) -> float:
    """Stage 3 Competing Seasonal, Photoperiod & Thermal Models (S1..S4): returns target lux multiplier."""
    if season_model == SEASON_MODEL_NEUTRAL:
        return 1.0

    # Photoperiod component: short winter days (<10h) boost daytime/morning target; long summer days (>15h) relax it
    day_len = outdoor.day_length_hours
    photoperiod_delta = clamp((12.0 - day_len) / 8.0, -0.6, 0.6)  # +0.5 in mid-winter, -0.5 in mid-summer
    if outdoor.time_regime in (REGIME_MORNING, REGIME_DAY):
        photo_mult = 1.0 + 0.28 * photoperiod_delta
    elif outdoor.time_regime == REGIME_EVENING:
        # In winter, cozy evening transitions earlier with slightly higher warm fill
        photo_mult = 1.0 + 0.12 * photoperiod_delta
    else:
        photo_mult = 1.0

    # Thermal-Luminance (Hue-Heat Hypothesis) component using outdoor + TRV-excluded indoor temperature:
    # Warm weather (>22C outside or >23C inside) -> "Cool Oasis" lower target (-15% to -25%)
    # Cold weather (<7C outside or <19C inside) -> "Warm Fill" higher target (+12% to +22%)
    temp_delta = 0.0
    if outdoor.outdoor_temp_c is not None:
        # Centered at 15°C outdoor comfort baseline
        temp_delta += clamp((15.0 - outdoor.outdoor_temp_c) / 14.0, -0.8, 0.8) * 0.65
    if indoor_temp_c is not None:
        # Centered at 21°C indoor standalone sensor baseline
        temp_delta += clamp((21.0 - indoor_temp_c) / 5.0, -0.8, 0.8) * 0.35

    # High indoor humidity (>68%) in warm weather amplifies muggy/over-lit feeling
    if indoor_humidity_pct is not None and indoor_humidity_pct > 68.0 and temp_delta < 0:
        temp_delta -= 0.08

    thermal_mult = clamp(1.0 + 0.24 * temp_delta, 0.72, 1.32)

    if season_model == SEASON_MODEL_PHOTOPERIOD:
        return round(clamp(photo_mult, 0.78, 1.30), 3)
    if season_model == SEASON_MODEL_THERMAL_HUE_HEAT:
        return round(clamp(thermal_mult, 0.72, 1.32), 3)
    if season_model == SEASON_MODEL_BIOCLIMATIC:
        return round(clamp(0.55 * photo_mult + 0.45 * thermal_mult, 0.72, 1.35), 3)

    return 1.0


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
    """Stage 1 Competing Base Illuminance & Retinal Adaptation Models (I1..I5).

    Returns (target_comfort_lux, total_perceptive_pct, natural_perceptive_pct).
    """
    # Base outdoor adaptation offset (lux)
    if daylight_mult <= 0.2 or outdoor.effective_sky_lux <= 5.0:
        adaptation_offset = 0.0
    else:
        adaptation_offset = (
            adaptation_alpha
            * daylight_mult
            * cave_weight
            * math.log10(1.0 + outdoor.effective_sky_lux / 120.0)
        )

    # Combine base target with time regime, weather model, season/thermal model, and nudge
    raw_target = (
        base_activity_lux * regime_mult * outdoor.circadian_factor + adaptation_offset
    )
    target_lux = max(2.0, raw_target * weather_mod * season_mod * nudge_multiplier)

    if illum_model == ILLUM_MODEL_STEVENS_POWER:
        # I2: Stevens' Power Law (psi ~ E^beta)
        # Exponent beta shifts from 0.35 in dark-adapted regimes to 0.45 in bright daytime
        beta = 0.44 if outdoor.is_daytime else 0.35
        target_psi = math.pow(max(1.0, target_lux), beta)
        # At E == target_lux -> 80% (Comfort threshold)
        total_pct = 80.0 * (math.pow(max(0.0, effective_spatial_lux), beta) / target_psi)
        nat_pct = 80.0 * (math.pow(max(0.0, natural_spatial_lux), beta) / target_psi)
        return target_lux, clamp(total_pct, 0.0, 100.0), clamp(nat_pct, 0.0, 100.0)

    if illum_model == ILLUM_MODEL_NAKA_RUSHTON:
        # I3: Naka-Rushton Retinal Sigmoid: P(E) = 100 * E^n / (E^n + sigma^n)
        # Set half-saturation sigma so that P(target_lux) == 80% -> sigma = target_lux / (4^(1/n))
        n_exp = 1.40
        sigma = max(1.5, target_lux / math.pow(4.0, 1.0 / n_exp))

        def _nr(e_val: float) -> float:
            if e_val <= 0.0:
                return 0.0
            en = math.pow(e_val, n_exp)
            sn = math.pow(sigma, n_exp)
            return 100.0 * (en / (en + sn))

        return target_lux, clamp(_nr(effective_spatial_lux), 0.0, 100.0), clamp(_nr(natural_spatial_lux), 0.0, 100.0)

    if illum_model == ILLUM_MODEL_CIE_DAYLIGHT:
        # I4: CIE Daylight Factor & Solar Penetration Model
        # Accounts for solar altitude geometry during daytime
        if outdoor.is_daytime and daylight_mult > 0.2:
            sin_el = math.sin(math.radians(clamp(outdoor.solar_elevation, 5.0, 85.0)))
            # Low sun penetrates deeper horizontally (boosts effective perceived room fill)
            depth_penetration = clamp(1.22 - 0.38 * sin_el, 0.85, 1.20)
        else:
            depth_penetration = 1.0
        adj_target = max(2.0, target_lux / depth_penetration)
        sat_lux = adj_target * 2.2
        denom = math.log1p(sat_lux / 4.0)
        total_pct = 100.0 * (math.log1p(effective_spatial_lux / 4.0) / denom)
        nat_pct = 100.0 * (math.log1p(natural_spatial_lux / 4.0) / denom)
        return adj_target, clamp(total_pct, 0.0, 100.0), clamp(nat_pct, 0.0, 100.0)

    if illum_model == ILLUM_MODEL_CUTTLE_MRSE:
        # I5: Kit Cuttle's Mean Room Surface Exitance (MRSE)
        # Penalizes high internal sensor contrast (glare vs shadow) and rewards diffuse sky bounce
        contrast_penalty = clamp(1.0 / math.pow(max(1.0, internal_contrast), 0.25), 0.60, 1.0)
        mrse_eff = effective_spatial_lux * contrast_penalty
        mrse_nat = natural_spatial_lux * contrast_penalty
        sat_lux = target_lux * 2.1
        denom = math.log1p(sat_lux / 5.0)
        total_pct = 100.0 * (math.log1p(mrse_eff / 5.0) / denom)
        nat_pct = 100.0 * (math.log1p(mrse_nat / 5.0) / denom)
        return target_lux, clamp(total_pct, 0.0, 100.0), clamp(nat_pct, 0.0, 100.0)

    # Default I1: ILLUM_MODEL_WEBER_FECHNER
    sat_lux = target_lux * 2.2
    denom = math.log1p(sat_lux / 5.0)
    total_pct = 100.0 * (math.log1p(effective_spatial_lux / 5.0) / denom)
    nat_pct = 100.0 * (math.log1p(natural_spatial_lux / 5.0) / denom)
    return target_lux, clamp(total_pct, 0.0, 100.0), clamp(nat_pct, 0.0, 100.0)


def compute_room_perceptive_illuminance(
    indoor_sensor_readings: dict[str, float],
    sensor_profiles: dict[str, SensorLearnedProfile],
    outdoor: OutdoorEnvironmentState,
    activity_key: str = ACTIVITY_RELAXING,
    daylight_level: str = DAYLIGHT_MEDIUM,
    sun_window_direction: str = "auto_learn",
    window_azimuth: float | None = None,
    artificial_lux_estimate: float = 0.0,
    min_artificial_lux: float = 12.0,
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
) -> RoomPerceptiveResult:
    """Compute Perceptive Illuminance, Natural Perceptive Illuminance, and Deficit for a room."""
    activity = ACTIVITY_DEFINITIONS.get(activity_key, ACTIVITY_DEFINITIONS[ACTIVITY_RELAXING])
    daylight_mult = DAYLIGHT_ADAPTATION_MULTIPLIERS.get(daylight_level, 1.0)
    regime = outdoor.time_regime
    regime_mult = REGIME_BASE_MULTIPLIERS.get(regime, 1.0) * regime_target_scale

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

    # 2. Process individual indoor lux sensors with Placement Multipliers AND Dynamic Regime Weighting
    calibrated_readings: dict[str, float] = {}
    dynamic_weights: dict[str, float] = {}
    natural_weights: dict[str, float] = {}

    for eid, raw_val in indoor_sensor_readings.items():
        prof = sensor_profiles.get(eid)
        placement = prof.placement if prof else "balanced"
        mult = PLACEMENT_MULTIPLIERS.get(placement, 1.0)
        if prof and placement == "auto_learn":
            mult = prof.learned_multiplier

        cal_val = max(0.0, raw_val * mult)
        calibrated_readings[eid] = cal_val

        if prof:
            w_total = prof.compute_dynamic_weight(
                raw_val,
                lights_are_on=lights_are_on,
                direct_sun_in_window=direct_sun,
                for_natural_lux=False,
            )
            w_nat = prof.compute_dynamic_weight(
                raw_val,
                lights_are_on=lights_are_on,
                direct_sun_in_window=direct_sun,
                for_natural_lux=True,
            )
        else:
            w_total = 1.0
            w_nat = 1.0

        dynamic_weights[eid] = w_total
        natural_weights[eid] = w_nat

    if calibrated_readings:
        raw_vals = list(indoor_sensor_readings.values())
        raw_lux = sum(raw_vals) / len(raw_vals)

        # Weighted spatial average using dynamic sensor weights (so high-floor 0lx sensors or flatlined sensors are down-weighted)
        total_w_sum = sum(dynamic_weights.values())
        if total_w_sum > 0.01:
            base_spatial_lux = (
                sum(calibrated_readings[k] * dynamic_weights[k] for k in calibrated_readings)
                / total_w_sum
            )
        else:
            base_spatial_lux = sum(calibrated_readings.values()) / len(calibrated_readings)

        # Multi-sensor internal contrast detection across trusted sensors (weight >= 0.25)
        trusted_vals = [
            calibrated_readings[k]
            for k, w in dynamic_weights.items()
            if w >= 0.25
        ] or list(calibrated_readings.values())

        if len(trusted_vals) >= 2:
            min_v = max(1.0, min(trusted_vals))
            max_v = max(1.0, max(trusted_vals))
            internal_contrast = max_v / min_v
            if internal_contrast > 3.0:
                geo_mean = math.sqrt(min_v * max_v)
                base_spatial_lux = 0.65 * geo_mean + 0.35 * base_spatial_lux
        else:
            internal_contrast = 1.0
    else:
        raw_lux = artificial_lux_estimate
        base_spatial_lux = artificial_lux_estimate
        internal_contrast = 1.0

    # 3. Direct vs. Diffuse Sky Factor
    if daylight_level == DAYLIGHT_NONE or not outdoor.is_daytime:
        diffuse_modifier = 1.0
    elif direct_sun:
        diffuse_modifier = 0.82
    else:
        diffuse_modifier = outdoor.diffuse_sky_factor * learned_diffuse_sensitivity

    effective_spatial_lux = max(0.0, base_spatial_lux * diffuse_modifier)

    # 4. Separate Natural Daylight Lux from Artificial Light Contribution
    #    Uses natural_weights (which suppresses lamp-blinded sensors when lights are ON)
    if calibrated_readings and lights_are_on:
        nat_w_sum = sum(natural_weights.values())
        if nat_w_sum > 0.01:
            nat_weighted_base = (
                sum(calibrated_readings[k] * natural_weights[k] for k in calibrated_readings)
                / nat_w_sum
            )
        else:
            nat_weighted_base = base_spatial_lux
    else:
        nat_weighted_base = base_spatial_lux

    effective_art_lux = min(
        max(0.0, artificial_lux_estimate),
        nat_weighted_base * 0.78,
    )
    natural_raw_est = max(0.0, nat_weighted_base - effective_art_lux)
    natural_spatial_lux = max(0.0, natural_raw_est * diffuse_modifier)

    # 5. Evaluate Factored Competing Models (Stage 2 Weather, Stage 3 Season/Temp, Stage 1 Illuminance)
    weather_mod = compute_weather_model_modifier(
        weather_model, outdoor, daylight_level, direct_sun
    )
    season_mod = compute_season_thermal_modifier(
        season_model, outdoor, indoor_temp_c, indoor_humidity_pct
    )

    base_target = float(activity["base_lux"]) * learned_activity_mult
    if not outdoor.is_daytime:
        base_target *= learned_night_scale
    alpha = float(activity["adaptation_alpha"]) * learned_outdoor_adaptation_scale
    cave_weight = float(activity["cave_contrast_weight"])

    active_nudge_pct = (
        feel_nudge_day_pct if outdoor.is_daytime else feel_nudge_night_pct
    )
    nudge_multiplier = math.pow(2.0, active_nudge_pct / 32.0)

    target_comfort_lux, perceptive_pct, natural_perceptive_pct = (
        compute_base_illuminance_model(
            illum_model=illum_model,
            effective_spatial_lux=effective_spatial_lux,
            natural_spatial_lux=natural_spatial_lux,
            base_activity_lux=base_target,
            adaptation_alpha=alpha,
            cave_weight=cave_weight,
            daylight_mult=daylight_mult,
            outdoor=outdoor,
            internal_contrast=internal_contrast,
            weather_mod=weather_mod,
            season_mod=season_mod,
            regime_mult=regime_mult,
            nudge_multiplier=nudge_multiplier,
        )
    )

    adaptation_offset = max(0.0, target_comfort_lux - (base_target * regime_mult))

    # 6. Lighting Deficit & Minimum Artificial Light Floor (Delta E_min) Decision Boundaries
    comfort_threshold_pct = 80.0
    if natural_perceptive_pct >= comfort_threshold_pct:
        deficit_pct = 0.0
    else:
        deficit_pct = clamp(
            100.0 * (comfort_threshold_pct - natural_perceptive_pct) / comfort_threshold_pct,
            0.0,
            100.0,
        )

    lux_deficit = max(0.0, target_comfort_lux - natural_spatial_lux)
    eff_min_art_lux = max(4.0, min_artificial_lux)

    # Rule A: When should lights turn ON (from OFF)?
    # Only turn lights ON if the lux deficit is at least as large as the minimum artificial light
    # our dimmest setting adds (or 18% of target_comfort_lux) AND perceptual deficit >= 16%.
    min_turn_on_lux_deficit = max(
        min(eff_min_art_lux * 0.85, target_comfort_lux * 0.45),
        target_comfort_lux * 0.18,
    )
    should_turn_on = (lux_deficit >= min_turn_on_lux_deficit) and (deficit_pct >= 16.0)

    # Rule B: When should lights turn OFF while the room is OCCUPIED?
    # Turn lights OFF while occupied as soon as Natural Daylight Alone reaches comfort (or when
    # the remaining lux deficit is smaller than 55% of the minimum artificial light quantum,
    # meaning keeping electric lights on even at minimum dimness over-lights the room).
    min_keep_on_lux_deficit = min(eff_min_art_lux * 0.55, target_comfort_lux * 0.15)
    should_turn_off_occupied = (
        (natural_spatial_lux >= target_comfort_lux * 1.02)
        or (lux_deficit <= min_keep_on_lux_deficit)
        or (deficit_pct <= 6.0)
    )

    # Classify Perceptive State
    if perceptive_pct >= 82.0:
        state = STATE_BRIGHT
    elif perceptive_pct >= 62.0:
        state = STATE_COMFORTABLE
    elif perceptive_pct >= 38.0:
        state = STATE_DIM
    else:
        state = STATE_DARK

    # 7. Recommended Brightness & Kruithof / Circadian Color Temperature (CCT)
    if deficit_pct <= 4.0 or should_turn_off_occupied:
        rec_brightness = 0
    else:
        norm_def = deficit_pct / 100.0
        scaled = (
            math.pow(norm_def, 0.78)
            * float(activity["brightness_scale"])
            * learned_brightness_scale
        )
        # In overnight regime, cap automatic brightness softly unless bright_task is selected
        if regime == REGIME_OVERNIGHT and activity_key != "bright_task":
            scaled = min(scaled, 0.25)
        rec_brightness = int(clamp(round(scaled * 100.0), 8, 100))

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
        artificial_lux_contribution=round(artificial_lux_estimate, 1),
        min_artificial_lux=round(eff_min_art_lux, 1),
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
        time_regime=regime,
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
