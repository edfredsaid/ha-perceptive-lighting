"""Factorial Competing-Models Engine & Event-Driven Gemini AI Calibrator (v1.3.0).

Architecture:
1. Strictly Event-Driven AI Policy (Zero Periodic API Cost):
   - AI (`ai_task.google_ai_task` / `conversation.google_generative_ai`) is NEVER called on a
     blind recurring timer.
   - It is only invoked:
     (a) When explicitly requested by the user ('AI: Optimize Room Model Now' button/service),
     (b) Once during initial room setup after historical Recorder profiling, or
     (c) After >= 5 user corrections when local residual error remains high (rate-limited to
         at most once per 24 hours).

2. Local Factorial Backtesting Across Competing Mathematical Models (Zero API Cost):
   - Partitioned into 4 distinct Time-of-Day Regimes: `morning`, `day`, `evening`, `overnight`.
   - Each regime selects from 3 orthogonal families of competing perceptual math:
     * Stage 1 Base Illuminance Models (I1..I5):
       `weber_fechner`, `stevens_power`, `naka_rushton`, `cie_daylight_factor`, `cuttle_mrse`
     * Stage 2 Weather & Sky Contrast Models (W1..W4):
       `cave_contrast`, `gloom_compensation`, `solar_altitude`, `weather_neutral`
     * Stage 3 Seasonal, Photoperiod & Thermal Models (S1..S4):
       `photoperiod_sad`, `thermal_hue_heat`, `combined_bioclimatic`, `season_neutral`
   - Variance Attribution Priority:
     When user feedback occurs (e.g. only on sunny afternoons), the local solver FIRST tests
     holding the Base Illuminance Model fixed while swapping Weather & Season/Thermal models
     (W x S). Only if errors span across weather regimes does it switch the Base Illuminance
     Model (I), minimizing arbitrary weight nudges!

3. AI Sensor Outlier & High Dark-Floor Evaluation:
   - Evaluates each sensor's Recorder statistics to infer room placement, configure smooth
     low-end dark-floor weight tapering (`floor_lux`, `dropout_knee_lux`), or flag dead/flatlined
     sensors for blacklisting (`suggest_blacklist`).
"""
from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timezone
import json
import logging
import math
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from homeassistant.core import HomeAssistant

from .const import (
    ACTIVITY_DEFINITIONS,
    DAYLIGHT_ADAPTATION_MULTIPLIERS,
    DAYLIGHT_MEDIUM,
    ILLUM_MODEL_CIE_DAYLIGHT,
    ILLUM_MODEL_CUTTLE_MRSE,
    ILLUM_MODEL_NAKA_RUSHTON,
    ILLUM_MODEL_STEVENS_POWER,
    ILLUM_MODEL_WEBER_FECHNER,
    ILLUM_MODELS,
    REGIME_DAY,
    REGIME_EVENING,
    REGIME_MORNING,
    REGIME_OVERNIGHT,
    SEASON_MODEL_BIOCLIMATIC,
    SEASON_MODEL_NEUTRAL,
    SEASON_MODEL_PHOTOPERIOD,
    SEASON_MODEL_THERMAL_HUE_HEAT,
    SEASON_MODELS,
    TIME_REGIMES,
    WEATHER_MODEL_CAVE_CONTRAST,
    WEATHER_MODEL_GLOOM_COMPENSATION,
    WEATHER_MODEL_NEUTRAL,
    WEATHER_MODEL_SOLAR_ALTITUDE,
    WEATHER_MODELS,
)
from .engine import (
    REGIME_BASE_MULTIPLIERS,
    OutdoorEnvironmentState,
    RoomPerceptiveResult,
    SensorLearnedProfile,
    clamp,
    compute_base_illuminance_model,
    compute_season_thermal_modifier,
    compute_weather_model_modifier,
)

_LOGGER = logging.getLogger(__name__)

MAX_OBSERVATION_HISTORY = 80
MIN_HOURS_BETWEEN_AUTO_AI_CALLS = 24.0
OBSERVATIONS_BEFORE_AUTO_AI_CALL = 5


@dataclass
class PerceptiveObservation:
    """A single recorded human interaction or calibration event in a room."""

    timestamp_utc: str
    event_type: str  # "manual_light_on", "manual_light_off", "manual_dim", "feels_dark_now", "feels_bright_now", "nudge_lighter", "nudge_darker"
    time_regime: str  # "morning", "day", "evening", "overnight"
    activity: str
    indoor_raw_lux: float
    natural_spatial_lux: float
    outdoor_sky_lux: float
    weather_condition: str
    cloud_cover_ratio: float
    diffuse_sky_factor: float
    solar_elevation: float
    solar_azimuth: float
    is_daytime: bool
    outdoor_temp_c: float | None
    indoor_temp_c: float | None
    indoor_humidity_pct: float | None
    day_length_hours: float
    direct_sun_in_window: bool
    internal_contrast_ratio: float
    model_target_lux: float
    model_deficit_pct: float
    model_recommended_bri_pct: int
    user_desired_lights_on: bool
    user_desired_bri_pct: int

    def to_dict(self) -> dict[str, Any]:
        return {
            "timestamp_utc": self.timestamp_utc,
            "event_type": self.event_type,
            "time_regime": self.time_regime,
            "activity": self.activity,
            "indoor_raw_lux": round(self.indoor_raw_lux, 1),
            "natural_spatial_lux": round(self.natural_spatial_lux, 1),
            "outdoor_sky_lux": round(self.outdoor_sky_lux, 1),
            "weather_condition": self.weather_condition,
            "cloud_cover_ratio": round(self.cloud_cover_ratio, 2),
            "diffuse_sky_factor": round(self.diffuse_sky_factor, 2),
            "solar_elevation": round(self.solar_elevation, 1),
            "solar_azimuth": round(self.solar_azimuth, 1),
            "is_daytime": self.is_daytime,
            "outdoor_temp_c": self.outdoor_temp_c,
            "indoor_temp_c": self.indoor_temp_c,
            "indoor_humidity_pct": self.indoor_humidity_pct,
            "day_length_hours": round(self.day_length_hours, 2),
            "direct_sun_in_window": self.direct_sun_in_window,
            "internal_contrast_ratio": round(self.internal_contrast_ratio, 2),
            "model_target_lux": round(self.model_target_lux, 1),
            "model_deficit_pct": round(self.model_deficit_pct, 1),
            "model_recommended_bri_pct": self.model_recommended_bri_pct,
            "user_desired_lights_on": self.user_desired_lights_on,
            "user_desired_bri_pct": self.user_desired_bri_pct,
        }

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> PerceptiveObservation:
        return cls(
            timestamp_utc=str(data.get("timestamp_utc", "")),
            event_type=str(data.get("event_type", "manual_light_on")),
            time_regime=str(data.get("time_regime", REGIME_DAY)),
            activity=str(data.get("activity", "relaxing")),
            indoor_raw_lux=float(data.get("indoor_raw_lux", 0.0)),
            natural_spatial_lux=float(data.get("natural_spatial_lux", 0.0)),
            outdoor_sky_lux=float(data.get("outdoor_sky_lux", 0.0)),
            weather_condition=str(data.get("weather_condition", "cloudy")),
            cloud_cover_ratio=float(data.get("cloud_cover_ratio", 0.5)),
            diffuse_sky_factor=float(data.get("diffuse_sky_factor", 1.0)),
            solar_elevation=float(data.get("solar_elevation", 15.0)),
            solar_azimuth=float(data.get("solar_azimuth", 180.0)),
            is_daytime=bool(data.get("is_daytime", True)),
            outdoor_temp_c=data.get("outdoor_temp_c"),
            indoor_temp_c=data.get("indoor_temp_c"),
            indoor_humidity_pct=data.get("indoor_humidity_pct"),
            day_length_hours=float(data.get("day_length_hours", 12.0)),
            direct_sun_in_window=bool(data.get("direct_sun_in_window", False)),
            internal_contrast_ratio=float(data.get("internal_contrast_ratio", 1.0)),
            model_target_lux=float(data.get("model_target_lux", 80.0)),
            model_deficit_pct=float(data.get("model_deficit_pct", 50.0)),
            model_recommended_bri_pct=int(data.get("model_recommended_bri_pct", 50)),
            user_desired_lights_on=bool(data.get("user_desired_lights_on", True)),
            user_desired_bri_pct=int(data.get("user_desired_bri_pct", 50)),
        )


@dataclass
class RegimeModelSpec:
    """Selected competing mathematical models and target scale for one Time-of-Day Regime."""

    illum_model: str = ILLUM_MODEL_WEBER_FECHNER
    weather_model: str = WEATHER_MODEL_CAVE_CONTRAST
    season_model: str = SEASON_MODEL_NEUTRAL
    target_scale: float = 1.0
    accuracy_pct: float = 100.0

    def to_dict(self) -> dict[str, Any]:
        return {
            "illum_model": self.illum_model,
            "weather_model": self.weather_model,
            "season_model": self.season_model,
            "target_scale": round(self.target_scale, 3),
            "accuracy_pct": round(self.accuracy_pct, 1),
        }

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> RegimeModelSpec:
        im = str(data.get("illum_model", ILLUM_MODEL_WEBER_FECHNER))
        wm = str(data.get("weather_model", WEATHER_MODEL_CAVE_CONTRAST))
        sm = str(data.get("season_model", SEASON_MODEL_NEUTRAL))
        return cls(
            illum_model=im if im in ILLUM_MODELS else ILLUM_MODEL_WEBER_FECHNER,
            weather_model=wm if wm in WEATHER_MODELS else WEATHER_MODEL_CAVE_CONTRAST,
            season_model=sm if sm in SEASON_MODELS else SEASON_MODEL_NEUTRAL,
            target_scale=clamp(float(data.get("target_scale", 1.0)), 0.35, 2.5),
            accuracy_pct=clamp(float(data.get("accuracy_pct", 100.0)), 0.0, 100.0),
        )


def _default_regime_models() -> dict[str, RegimeModelSpec]:
    return {
        REGIME_MORNING: RegimeModelSpec(
            illum_model=ILLUM_MODEL_STEVENS_POWER,
            weather_model=WEATHER_MODEL_GLOOM_COMPENSATION,
            season_model=SEASON_MODEL_PHOTOPERIOD,
        ),
        REGIME_DAY: RegimeModelSpec(
            illum_model=ILLUM_MODEL_NAKA_RUSHTON,
            weather_model=WEATHER_MODEL_CAVE_CONTRAST,
            season_model=SEASON_MODEL_BIOCLIMATIC,
        ),
        REGIME_EVENING: RegimeModelSpec(
            illum_model=ILLUM_MODEL_WEBER_FECHNER,
            weather_model=WEATHER_MODEL_NEUTRAL,
            season_model=SEASON_MODEL_THERMAL_HUE_HEAT,
        ),
        REGIME_OVERNIGHT: RegimeModelSpec(
            illum_model=ILLUM_MODEL_STEVENS_POWER,
            weather_model=WEATHER_MODEL_NEUTRAL,
            season_model=SEASON_MODEL_NEUTRAL,
        ),
    }


@dataclass
class LearnedRoomWeights:
    """Multi-dimensional learned perceptual weights and per-regime competing model selections."""

    activity_target_multipliers: dict[str, float] = field(default_factory=dict)
    outdoor_adaptation_scale: float = 1.0
    diffuse_sky_sensitivity: float = 1.0
    night_comfort_scale: float = 1.0
    brightness_output_scale: float = 1.0
    regime_models: dict[str, RegimeModelSpec] = field(
        default_factory=_default_regime_models
    )
    last_ai_insight: str = ""
    last_ai_optimized_utc: str | None = None
    total_optimizations: int = 0
    initial_ai_audit_done: bool = False

    def get_activity_multiplier(self, activity: str) -> float:
        return clamp(
            float(self.activity_target_multipliers.get(activity, 1.0)),
            0.25,
            3.0,
        )

    def get_regime_spec(self, regime: str) -> RegimeModelSpec:
        if regime not in self.regime_models:
            self.regime_models[regime] = _default_regime_models().get(
                regime, RegimeModelSpec()
            )
        return self.regime_models[regime]

    def can_auto_call_ai_now(self) -> bool:
        """Return True if at least MIN_HOURS_BETWEEN_AUTO_AI_CALLS have elapsed since the last AI call."""
        if not self.last_ai_optimized_utc:
            return True
        try:
            last_dt = datetime.fromisoformat(self.last_ai_optimized_utc)
            if last_dt.tzinfo is None:
                last_dt = last_dt.replace(tzinfo=timezone.utc)
            elapsed_hrs = (
                datetime.now(timezone.utc) - last_dt
            ).total_seconds() / 3600.0
            return elapsed_hrs >= MIN_HOURS_BETWEEN_AUTO_AI_CALLS
        except Exception:  # noqa: BLE001
            return True

    def to_dict(self) -> dict[str, Any]:
        return {
            "activity_target_multipliers": {
                k: round(v, 3) for k, v in self.activity_target_multipliers.items()
            },
            "outdoor_adaptation_scale": round(self.outdoor_adaptation_scale, 3),
            "diffuse_sky_sensitivity": round(self.diffuse_sky_sensitivity, 3),
            "night_comfort_scale": round(self.night_comfort_scale, 3),
            "brightness_output_scale": round(self.brightness_output_scale, 3),
            "regime_models": {
                k: v.to_dict() for k, v in self.regime_models.items()
            },
            "last_ai_insight": self.last_ai_insight,
            "last_ai_optimized_utc": self.last_ai_optimized_utc,
            "total_optimizations": self.total_optimizations,
            "initial_ai_audit_done": self.initial_ai_audit_done,
        }

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> LearnedRoomWeights:
        if not isinstance(data, dict):
            return cls()
        raw_mults = data.get("activity_target_multipliers", {})
        mults = (
            {
                str(k): clamp(float(v), 0.25, 3.0)
                for k, v in raw_mults.items()
                if k in ACTIVITY_DEFINITIONS
            }
            if isinstance(raw_mults, dict)
            else {}
        )
        rmodels = _default_regime_models()
        raw_rmodels = data.get("regime_models", {})
        if isinstance(raw_rmodels, dict):
            for rk, rv in raw_rmodels.items():
                if rk in TIME_REGIMES and isinstance(rv, dict):
                    rmodels[rk] = RegimeModelSpec.from_dict(rv)

        return cls(
            activity_target_multipliers=mults,
            outdoor_adaptation_scale=clamp(
                float(data.get("outdoor_adaptation_scale", 1.0)), 0.20, 2.5
            ),
            diffuse_sky_sensitivity=clamp(
                float(data.get("diffuse_sky_sensitivity", 1.0)), 0.65, 1.50
            ),
            night_comfort_scale=clamp(
                float(data.get("night_comfort_scale", 1.0)), 0.30, 2.2
            ),
            brightness_output_scale=clamp(
                float(data.get("brightness_output_scale", 1.0)), 0.40, 1.80
            ),
            regime_models=rmodels,
            last_ai_insight=str(data.get("last_ai_insight", "")),
            last_ai_optimized_utc=data.get("last_ai_optimized_utc"),
            total_optimizations=int(data.get("total_optimizations", 0)),
            initial_ai_audit_done=bool(data.get("initial_ai_audit_done", False)),
        )


def _score_candidate_combo_on_observation(
    obs: PerceptiveObservation,
    illum_model: str,
    weather_model: str,
    season_model: str,
    daylight_level: str,
    min_artificial_lux: float,
    target_scale: float = 1.0,
) -> tuple[float, float]:
    """Evaluate a single (I, W, S) candidate model triplet on a historical observation.

    Returns (loss, required_scale_to_match_user).
    Lower loss = better natural prediction WITHOUT needing manual nudges.
    """
    activity = ACTIVITY_DEFINITIONS.get(
        obs.activity, ACTIVITY_DEFINITIONS["relaxing"]
    )
    daylight_mult = DAYLIGHT_ADAPTATION_MULTIPLIERS.get(daylight_level, 1.0)
    regime_mult = REGIME_BASE_MULTIPLIERS.get(obs.time_regime, 1.0) * target_scale

    synth_outdoor = OutdoorEnvironmentState(
        raw_outdoor_lux=obs.outdoor_sky_lux,
        effective_sky_lux=obs.outdoor_sky_lux,
        weather_condition=obs.weather_condition,
        cloud_cover_ratio=obs.cloud_cover_ratio,
        diffuse_sky_factor=obs.diffuse_sky_factor,
        solar_elevation=obs.solar_elevation,
        solar_azimuth=obs.solar_azimuth,
        is_daytime=obs.is_daytime,
        circadian_factor=0.95 if obs.is_daytime else 0.55,
        outdoor_temp_c=obs.outdoor_temp_c,
        day_length_hours=obs.day_length_hours,
        time_regime=obs.time_regime,
    )

    w_mod = compute_weather_model_modifier(
        weather_model, synth_outdoor, daylight_level, obs.direct_sun_in_window
    )
    s_mod = compute_season_thermal_modifier(
        season_model, synth_outdoor, obs.indoor_temp_c, obs.indoor_humidity_pct
    )

    target_lux, _, nat_pct = compute_base_illuminance_model(
        illum_model=illum_model,
        effective_spatial_lux=obs.natural_spatial_lux,
        natural_spatial_lux=obs.natural_spatial_lux,
        base_activity_lux=float(activity["base_lux"]),
        adaptation_alpha=float(activity["adaptation_alpha"]),
        cave_weight=float(activity["cave_contrast_weight"]),
        daylight_mult=daylight_mult,
        outdoor=synth_outdoor,
        internal_contrast=obs.internal_contrast_ratio,
        weather_mod=w_mod,
        season_mod=s_mod,
        regime_mult=regime_mult,
        nudge_multiplier=1.0,
    )

    deficit_pct = max(0.0, 100.0 * (80.0 - nat_pct) / 80.0) if nat_pct < 80.0 else 0.0
    lux_deficit = max(0.0, target_lux - obs.natural_spatial_lux)
    min_turn_on = max(min_artificial_lux * 0.85, target_lux * 0.18)
    pred_lights_on = (lux_deficit >= min_turn_on) and (deficit_pct >= 16.0)

    # Ideal target lux implied by user's action
    nat = max(1.0, obs.natural_spatial_lux)
    if obs.user_desired_lights_on:
        # User wanted lights ON -> target_lux should be at least nat + min_artificial_lux * 1.15
        desired_min_target = max(nat * 1.45, nat + min_artificial_lux * 1.15)
        if target_lux >= desired_min_target and pred_lights_on:
            loss = 0.0
            req_scale = 1.0
        else:
            req_scale = clamp(desired_min_target / max(1.0, target_lux), 1.0, 2.5)
            loss = abs(math.log2(req_scale)) + (0.6 if not pred_lights_on else 0.0)
    else:
        # User wanted lights OFF -> target_lux should be <= nat * 0.88
        desired_max_target = max(2.0, nat * 0.88)
        if target_lux <= desired_max_target and not pred_lights_on:
            loss = 0.0
            req_scale = 1.0
        else:
            req_scale = clamp(desired_max_target / max(2.0, target_lux), 0.35, 1.0)
            loss = abs(math.log2(req_scale)) + (0.6 if pred_lights_on else 0.0)

    return loss, req_scale


def run_local_factorial_backtest(
    weights: LearnedRoomWeights,
    observations: list[PerceptiveObservation],
    daylight_level: str = DAYLIGHT_MEDIUM,
    min_artificial_lux: float = 12.0,
    active_regime: str | None = None,
) -> dict[str, Any]:
    """Run local factorial backtesting across (I x W x S) models per Time-of-Day Regime.

    Tests Weather & Season models FIRST (holding Base Illuminance Model fixed) to see if
    weather/season/temperature explains the user's feedback before changing the base illuminance model!
    """
    regimes_to_test = [active_regime] if active_regime in TIME_REGIMES else list(TIME_REGIMES)
    leaderboard_summary: dict[str, Any] = {}

    for regime in regimes_to_test:
        reg_obs = [o for o in observations if o.time_regime == regime][-25:]
        if not reg_obs:
            continue

        current_spec = weights.get_regime_spec(regime)

        # Step 1: Score the current (I, W, S) model on this regime's observations
        cur_losses = [
            _score_candidate_combo_on_observation(
                o,
                current_spec.illum_model,
                current_spec.weather_model,
                current_spec.season_model,
                daylight_level,
                min_artificial_lux,
                1.0,
            )[0]
            for o in reg_obs
        ]
        cur_mean_loss = sum(cur_losses) / len(cur_losses)

        # Step 2: Priority 1 — Test holding current_spec.illum_model FIXED and varying (W x S)
        #         This checks if a different Weather or Season/Thermal model explains the errors!
        best_ws_loss = cur_mean_loss
        best_ws_combo = (
            current_spec.illum_model,
            current_spec.weather_model,
            current_spec.season_model,
        )
        best_ws_scales: list[float] = [1.0]

        for wm in WEATHER_MODELS:
            for sm in SEASON_MODELS:
                evals = [
                    _score_candidate_combo_on_observation(
                        o,
                        current_spec.illum_model,
                        wm,
                        sm,
                        daylight_level,
                        min_artificial_lux,
                        1.0,
                    )
                    for o in reg_obs
                ]
                m_loss = sum(e[0] for e in evals) / len(evals)
                if m_loss < best_ws_loss - 0.04:
                    best_ws_loss = m_loss
                    best_ws_combo = (current_spec.illum_model, wm, sm)
                    best_ws_scales = [e[1] for e in evals]

        # Step 3: Priority 2 — Full (I x W x S) 80-model grid search
        #         Only replace the Base Illuminance Model (I) if it improves loss substantially (>0.10)
        #         over the best Weather/Season swap!
        best_full_loss = best_ws_loss
        best_full_combo = best_ws_combo
        best_full_scales = best_ws_scales

        for im in ILLUM_MODELS:
            for wm in WEATHER_MODELS:
                for sm in SEASON_MODELS:
                    evals = [
                        _score_candidate_combo_on_observation(
                            o, im, wm, sm, daylight_level, min_artificial_lux, 1.0
                        )
                        for o in reg_obs
                    ]
                    m_loss = sum(e[0] for e in evals) / len(evals)
                    if m_loss < best_full_loss - 0.10:
                        best_full_loss = m_loss
                        best_full_combo = (im, wm, sm)
                        best_full_scales = [e[1] for e in evals]

        # Geometric mean of residual scale needed after picking the best competing models
        geo_scale = math.exp(
            sum(math.log(clamp(s, 0.35, 2.5)) for s in best_full_scales)
            / len(best_full_scales)
        )
        # Blend gently with existing target_scale
        new_scale = clamp(0.45 * current_spec.target_scale + 0.55 * geo_scale, 0.40, 2.20)
        acc_pct = clamp(100.0 * math.exp(-best_full_loss * 0.85), 10.0, 100.0)

        current_spec.illum_model = best_full_combo[0]
        current_spec.weather_model = best_full_combo[1]
        current_spec.season_model = best_full_combo[2]
        current_spec.target_scale = round(new_scale, 3)
        current_spec.accuracy_pct = round(acc_pct, 1)

        leaderboard_summary[regime] = {
            "observations": len(reg_obs),
            "selected_illum_model": current_spec.illum_model,
            "selected_weather_model": current_spec.weather_model,
            "selected_season_model": current_spec.season_model,
            "residual_target_scale": current_spec.target_scale,
            "natural_accuracy_pct": current_spec.accuracy_pct,
        }

    return leaderboard_summary


def apply_online_sgd_step(
    weights: LearnedRoomWeights,
    obs: PerceptiveObservation,
    daylight_level: str = DAYLIGHT_MEDIUM,
    min_artificial_lux: float = 12.0,
) -> None:
    """Run immediate local Factorial Model Selection + light contextual residual update."""
    # 1. First run Factorial Backtesting for the active Time-of-Day Regime so Weather/Season/Illuminance
    #    competing models absorb as much of the user's preference as possible!
    run_local_factorial_backtest(
        weights=weights,
        observations=[obs],
        daylight_level=daylight_level,
        min_artificial_lux=min_artificial_lux,
        active_regime=obs.time_regime,
    )

    # 2. Gentle residual update on activity/environmental multipliers
    lr = 0.14 if obs.event_type in ("feels_dark_now", "feels_bright_now") else 0.08
    if obs.user_desired_lights_on:
        direction = 1.0
    else:
        direction = -1.0

    cur_act = weights.get_activity_multiplier(obs.activity)
    weights.activity_target_multipliers[obs.activity] = clamp(
        cur_act * math.pow(2.0, direction * lr * 0.45), 0.30, 2.50
    )

    if obs.is_daytime and obs.outdoor_sky_lux > 800.0:
        sky_feature = clamp(math.log10(obs.outdoor_sky_lux / 500.0) / 2.0, 0.1, 1.0)
        weights.outdoor_adaptation_scale = clamp(
            weights.outdoor_adaptation_scale
            * math.pow(2.0, direction * lr * 0.55 * sky_feature),
            0.20,
            2.50,
        )

    if obs.is_daytime and obs.cloud_cover_ratio >= 0.55:
        weights.diffuse_sky_sensitivity = clamp(
            weights.diffuse_sky_sensitivity
            * math.pow(2.0, -direction * lr * 0.25 * obs.cloud_cover_ratio),
            0.65,
            1.50,
        )

    if not obs.is_daytime:
        weights.night_comfort_scale = clamp(
            weights.night_comfort_scale * math.pow(2.0, direction * lr * 0.45),
            0.30,
            2.20,
        )

    if not weights.last_ai_insight:
        rspec = weights.get_regime_spec(obs.time_regime)
        weights.last_ai_insight = (
            f"Local Factorial Fit ({obs.time_regime.title()}): "
            f"{ILLUM_MODELS.get(rspec.illum_model, rspec.illum_model)} + "
            f"{WEATHER_MODELS.get(rspec.weather_model, rspec.weather_model)} "
            f"(Scale {rspec.target_scale:.2f}x)"
        )


async def async_run_gemini_room_optimization(
    hass: HomeAssistant,
    room_name: str,
    room_config: dict[str, Any],
    weights: LearnedRoomWeights,
    observations: list[PerceptiveObservation],
    sensor_profiles: dict[str, SensorLearnedProfile],
    current_outdoor: OutdoorEnvironmentState,
    current_result: RoomPerceptiveResult,
) -> bool:
    """Use Home Assistant's Gemini AI (`ai_task` / `conversation`) to calibrate competing models & sensor weights."""
    # First run full local factorial backtest across all recorded observations
    daylight_level = room_config.get("daylight_level", DAYLIGHT_MEDIUM)
    factorial_summary = run_local_factorial_backtest(
        weights=weights,
        observations=observations,
        daylight_level=daylight_level,
        min_artificial_lux=current_result.min_artificial_lux,
    )

    recent_obs = [o.to_dict() for o in observations[-20:]]
    sensor_summary = {
        eid: prof.to_dict() for eid, prof in sensor_profiles.items()
    }

    prompt = (
        f"You are the AI Calibration Architect for the 'Perceptive Lighting' Home Assistant integration "
        f"in room '{room_name}'.\n\n"
        f"Your two tasks are:\n"
        f"1. COMPETING MATHEMATICAL MODEL SELECTION PER TIME-OF-DAY REGIME (`morning`, `day`, `evening`, `overnight`):\n"
        f"   Instead of blunt fudge-factor offsets, select the best combination of:\n"
        f"   - Base Illuminance Model (`illum_model`): {list(ILLUM_MODELS.keys())}\n"
        f"   - Weather & Sky Contrast Model (`weather_model`): {list(WEATHER_MODELS.keys())}\n"
        f"   - Seasonal, Photoperiod & Thermal Model (`season_model`): {list(SEASON_MODELS.keys())}\n"
        f"   Examine when the user made adjustments. For example, if the user only wanted lights OFF on sunny afternoons "
        f"   in the `day` regime, prefer switching `weather_model` to `gloom_compensation` or `solar_altitude` and keeping "
        f"   `target_scale` near 1.0, rather than distorting other regimes!\n\n"
        f"2. SENSOR PLACEMENT, HIGH DARK-FLOOR TAPERING & OUTLIER EVALUATION:\n"
        f"   Review each room sensor's profile in `sensor_profiles` and current readings:\n"
        f"   - Estimate where each sensor sits (`ai_location_guess`, e.g. 'Near Window', 'Shaded Shelf', 'High Dark-Floor Sensor').\n"
        f"   - If a sensor drops to 0 lx early at dusk while still useful in brighter light, set `floor_lux` (e.g. 1.0-3.0) and "
        f"     `dropout_knee_lux` (e.g. 10.0-18.0) so its weight smoothly tapers down as the room gets darker.\n"
        f"   - If a sensor is permanently stuck at 0 lx or useless, set `base_trust_weight` to 0.0 and `suggest_blacklist` to true.\n\n"
        f"Room Configuration & Telemetry:\n"
        f"- Daylight Exposure: {daylight_level}\n"
        f"- Current Time Regime: {current_outdoor.time_regime} | Outdoor Sky: {current_outdoor.effective_sky_lux} lx "
        f"({current_outdoor.weather_condition}, clouds={current_outdoor.cloud_cover_ratio}, T_out={current_outdoor.outdoor_temp_c}C, "
        f"DayLength={current_outdoor.day_length_hours}h)\n"
        f"- Standalone Room Temp: {current_result.indoor_temp_c}C | Humidity: {current_result.indoor_humidity_pct}%\n"
        f"- Sensor Profiles: {json.dumps(sensor_summary)}\n"
        f"- Live Sensor Readings (lx): {json.dumps(current_result.sensor_breakdown)}\n"
        f"- Local Factorial Backtest Summary: {json.dumps(factorial_summary)}\n"
        f"- Recent User Calibration & Override Events ({len(recent_obs)}): {json.dumps(recent_obs)}\n\n"
        f"Return a JSON object with keys:\n"
        f"- `regime_models`: object mapping regime ('morning', 'day', 'evening', 'overnight') to "
        f"{{'illum_model': str, 'weather_model': str, 'season_model': str, 'target_scale': float}}\n"
        f"- `sensor_updates`: object mapping sensor entity_id to "
        f"{{'ai_location_guess': str, 'floor_lux': float, 'dropout_knee_lux': float, 'base_trust_weight': float, 'suggest_blacklist': bool}}\n"
        f"- `outdoor_adaptation_scale`: float (0.20 to 2.50)\n"
        f"- `diffuse_sky_sensitivity`: float (0.65 to 1.50)\n"
        f"- `insight`: concise 1-2 sentence explanation of which models & sensor weights were chosen and why."
    )

    parsed_data: dict[str, Any] | None = None

    # Method 1: Try `ai_task.generate_data`
    if hass.services.has_service("ai_task", "generate_data"):
        ai_entities = [
            st.entity_id
            for st in hass.states.async_all("ai_task")
            if st.state not in ("unavailable", "unknown")
        ]
        target_ai_entity = ai_entities[0] if ai_entities else "ai_task.google_ai_task"
        try:
            resp = await hass.services.async_call(
                "ai_task",
                "generate_data",
                {
                    "entity_id": target_ai_entity,
                    "task_name": f"perceptive_lighting_{room_name}_calibration",
                    "instructions": prompt + "\nRespond ONLY with valid JSON.",
                },
                blocking=True,
                return_response=True,
            )
            if isinstance(resp, dict):
                raw_data = resp.get("data", resp)
                if isinstance(raw_data, dict):
                    parsed_data = raw_data
                elif isinstance(raw_data, str):
                    cleaned = raw_data.strip()
                    if "```" in cleaned:
                        cleaned = cleaned.split("```")[1]
                        if cleaned.startswith("json"):
                            cleaned = cleaned[4:]
                    parsed_data = json.loads(cleaned.strip())
        except Exception as err:  # noqa: BLE001
            _LOGGER.debug("[%s] ai_task.generate_data fallback: %s", room_name, err)

    # Method 2: Fallback to `conversation.process`
    if parsed_data is None and hass.services.has_service("conversation", "process"):
        conv_entities = [
            st.entity_id
            for st in hass.states.async_all("conversation")
            if "google" in st.entity_id or "gemini" in st.entity_id or "openai" in st.entity_id
        ]
        if conv_entities:
            try:
                resp = await hass.services.async_call(
                    "conversation",
                    "process",
                    {
                        "agent_id": conv_entities[0],
                        "text": prompt + "\nRespond ONLY with a raw JSON object.",
                    },
                    blocking=True,
                    return_response=True,
                )
                speech = (
                    resp.get("response", {})
                    .get("speech", {})
                    .get("plain", {})
                    .get("speech", "")
                    if isinstance(resp, dict)
                    else ""
                )
                if speech:
                    cleaned = speech.strip()
                    if "```" in cleaned:
                        cleaned = cleaned.split("```")[1]
                        if cleaned.startswith("json"):
                            cleaned = cleaned[4:]
                    parsed_data = json.loads(cleaned.strip())
            except Exception as err:  # noqa: BLE001
                _LOGGER.debug("[%s] conversation.process fallback: %s", room_name, err)

    if isinstance(parsed_data, dict):
        # 1. Apply per-regime competing model selections from Gemini
        rm_updates = parsed_data.get("regime_models")
        if isinstance(rm_updates, dict):
            for rk, rv in rm_updates.items():
                if rk in TIME_REGIMES and isinstance(rv, dict):
                    spec = weights.get_regime_spec(rk)
                    if rv.get("illum_model") in ILLUM_MODELS:
                        spec.illum_model = rv["illum_model"]
                    if rv.get("weather_model") in WEATHER_MODELS:
                        spec.weather_model = rv["weather_model"]
                    if rv.get("season_model") in SEASON_MODELS:
                        spec.season_model = rv["season_model"]
                    if "target_scale" in rv:
                        spec.target_scale = clamp(
                            0.35 * spec.target_scale + 0.65 * float(rv["target_scale"]),
                            0.40,
                            2.20,
                        )

        # 2. Apply per-sensor dynamic curve & outlier updates from Gemini
        s_updates = parsed_data.get("sensor_updates")
        if isinstance(s_updates, dict):
            for eid, sdata in s_updates.items():
                if eid in sensor_profiles and isinstance(sdata, dict):
                    prof = sensor_profiles[eid]
                    if sdata.get("ai_location_guess"):
                        prof.ai_location_guess = str(sdata["ai_location_guess"])[:100]
                    if "floor_lux" in sdata:
                        prof.floor_lux = clamp(float(sdata["floor_lux"]), 0.0, 50.0)
                    if "dropout_knee_lux" in sdata:
                        prof.dropout_knee_lux = clamp(float(sdata["dropout_knee_lux"]), 0.0, 120.0)
                    if "base_trust_weight" in sdata:
                        prof.base_trust_weight = clamp(float(sdata["base_trust_weight"]), 0.0, 1.0)
                    if "suggest_blacklist" in sdata:
                        prof.suggest_blacklist = bool(sdata["suggest_blacklist"])

        if "outdoor_adaptation_scale" in parsed_data:
            weights.outdoor_adaptation_scale = clamp(
                0.35 * weights.outdoor_adaptation_scale
                + 0.65 * float(parsed_data["outdoor_adaptation_scale"]),
                0.20,
                2.50,
            )
        if "diffuse_sky_sensitivity" in parsed_data:
            weights.diffuse_sky_sensitivity = clamp(
                0.35 * weights.diffuse_sky_sensitivity
                + 0.65 * float(parsed_data["diffuse_sky_sensitivity"]),
                0.65,
                1.50,
            )

        insight = str(parsed_data.get("insight", "")).strip()
        if insight:
            weights.last_ai_insight = insight[:260]
        weights.last_ai_optimized_utc = datetime.now(timezone.utc).isoformat()
        weights.total_optimizations += 1
        weights.initial_ai_audit_done = True
        _LOGGER.info(
            "[%s] Gemini AI calibrated room models & sensors: %s",
            room_name,
            weights.last_ai_insight,
        )
        return True

    # Fallback if no AI service responded: use our local Factorial Backtest results
    rspec = weights.get_regime_spec(current_outdoor.time_regime)
    weights.last_ai_insight = (
        f"Factorial Fit ({current_outdoor.time_regime.title()}): "
        f"{ILLUM_MODELS.get(rspec.illum_model, rspec.illum_model)} + "
        f"{WEATHER_MODELS.get(rspec.weather_model, rspec.weather_model)} + "
        f"{SEASON_MODELS.get(rspec.season_model, rspec.season_model)}"
    )
    weights.last_ai_optimized_utc = datetime.now(timezone.utc).isoformat()
    weights.total_optimizations += 1
    weights.initial_ai_audit_done = True
    return False
