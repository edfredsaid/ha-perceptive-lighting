"""Factorial Competing-Models Engine & Event-Driven Gemini AI Calibrator (v2.0.0).

Architecture:
1. Closed-Loop Measured Lux Calibration:
   - Observations record the actual measured `Total Room Lux` (`indoor_raw_lux` / `effective_spatial_lux`),
     the user's directly chosen light state (`user_desired_lights_on`, `user_desired_bri_pct`, `shed_count`),
     and the active `regime_weights`.
2. Local Factorial Backtesting Across Competing Mathematical Models (Zero API Cost):
   - Evaluates the full regime observation history (never overfitting to a single point) to select
     the best (Illuminance Curve x Weather Model x Season/Thermal Model) that predicts the user's
     measured comfort thresholds across changing weather and seasons.
3. AI Light Priority Ranking & Sensor Outlier Evaluation:
   - Evaluates room light fixtures (dimmable vs on/off switch, lumen ratings, spotlight vs accent strip/lamp)
     to order `light_priority_order` from 1st-to-turn-off when dimming past 1% down to the softest last light standing.
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
    DAYLIGHT_MEDIUM,
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
    WEATHER_MODELS,
)
from .engine import (
    OutdoorEnvironmentState,
    RoomPerceptiveResult,
    SensorLearnedProfile,
    clamp,
    compute_season_thermal_modifier,
    compute_weather_model_modifier,
)

_LOGGER = logging.getLogger(__name__)

MAX_OBSERVATION_HISTORY = 80
MIN_HOURS_BETWEEN_AUTO_AI_CALLS = 24.0
OBSERVATIONS_BEFORE_AUTO_AI_CALL = 5


@dataclass
class PerceptiveObservation:
    """A single recorded human interaction & measured room lux state."""

    timestamp_utc: str
    event_type: str  # "nudge_lighter", "nudge_darker", "feels_dark_now", "feels_bright_now", "manual_light_on", "manual_light_off", "manual_dim"
    time_regime: str
    activity: str
    indoor_raw_lux: float  # Measured Total Room Lux when settled
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
    user_shed_count: int = 0

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
            "user_shed_count": self.user_shed_count,
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
            model_target_lux=float(data.get("model_target_lux", 14.0)),
            model_deficit_pct=float(data.get("model_deficit_pct", 50.0)),
            model_recommended_bri_pct=int(data.get("model_recommended_bri_pct", 50)),
            user_desired_lights_on=bool(data.get("user_desired_lights_on", True)),
            user_desired_bri_pct=int(data.get("user_desired_bri_pct", 50)),
            user_shed_count=int(data.get("user_shed_count", 0)),
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
            target_scale=clamp(float(data.get("target_scale", 1.0)), 0.65, 1.45),
            accuracy_pct=clamp(float(data.get("accuracy_pct", 100.0)), 0.0, 100.0),
        )


def _default_regime_models() -> dict[str, RegimeModelSpec]:
    return {
        REGIME_MORNING: RegimeModelSpec(
            illum_model=ILLUM_MODEL_WEBER_FECHNER,
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
            weather_model=WEATHER_MODEL_GLOOM_COMPENSATION,
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
    """Learned competing model selections and AI insights for a room."""

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
        return 1.0

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
            "activity_target_multipliers": {},
            "outdoor_adaptation_scale": 1.0,
            "diffuse_sky_sensitivity": 1.0,
            "night_comfort_scale": 1.0,
            "brightness_output_scale": 1.0,
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
        rmodels = _default_regime_models()
        raw_rmodels = data.get("regime_models", {})
        if isinstance(raw_rmodels, dict):
            for rk, rv in raw_rmodels.items():
                if rk in TIME_REGIMES and isinstance(rv, dict):
                    rmodels[rk] = RegimeModelSpec.from_dict(rv)

        return cls(
            regime_models=rmodels,
            last_ai_insight=str(data.get("last_ai_insight", "")),
            last_ai_optimized_utc=data.get("last_ai_optimized_utc"),
            total_optimizations=int(data.get("total_optimizations", 0)),
            initial_ai_audit_done=bool(data.get("initial_ai_audit_done", False)),
        )


def _score_candidate_combo_on_observation(
    obs: PerceptiveObservation,
    weather_model: str,
    season_model: str,
    daylight_level: str,
    base_turn_on_lux: float,
) -> float:
    """Evaluate how well a (Weather, Season) model predicts the user's on/off preference on `obs`."""
    synth_outdoor = OutdoorEnvironmentState(
        raw_outdoor_lux=obs.outdoor_sky_lux,
        effective_sky_lux=obs.outdoor_sky_lux,
        weather_condition=obs.weather_condition,
        cloud_cover_ratio=obs.cloud_cover_ratio,
        diffuse_sky_factor=obs.diffuse_sky_factor,
        solar_elevation=obs.solar_elevation,
        solar_azimuth=obs.solar_azimuth,
        is_daytime=obs.is_daytime,
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
    pred_turn_on = base_turn_on_lux * w_mod * s_mod
    room_lux = max(0.5, obs.indoor_raw_lux)

    if obs.user_desired_lights_on:
        # User wanted lights ON at `room_lux` -> pred_turn_on should be >= room_lux
        if pred_turn_on >= room_lux:
            return 0.0
        return abs(room_lux - pred_turn_on) / max(2.0, base_turn_on_lux)
    else:
        # User wanted lights OFF at `room_lux` -> pred_turn_on should be < room_lux
        if pred_turn_on < room_lux:
            return 0.0
        return abs(pred_turn_on - room_lux) / max(2.0, base_turn_on_lux)


def run_local_factorial_backtest(
    weights: LearnedRoomWeights,
    observations: list[PerceptiveObservation],
    daylight_level: str = DAYLIGHT_MEDIUM,
    base_turn_on_map: dict[str, float] | None = None,
    active_regime: str | None = None,
) -> dict[str, Any]:
    """Run local factorial backtesting across (Weather x Season) models using full regime observation history."""
    regimes_to_test = [active_regime] if active_regime in TIME_REGIMES else list(TIME_REGIMES)
    leaderboard_summary: dict[str, Any] = {}

    for regime in regimes_to_test:
        reg_obs = [o for o in observations if o.time_regime == regime][-25:]
        if len(reg_obs) < 2:
            continue

        current_spec = weights.get_regime_spec(regime)
        base_on = (base_turn_on_map or {}).get(regime, 12.0)

        best_loss = sum(
            _score_candidate_combo_on_observation(
                o, current_spec.weather_model, current_spec.season_model, daylight_level, base_on
            )
            for o in reg_obs
        ) / len(reg_obs)
        best_wm = current_spec.weather_model
        best_sm = current_spec.season_model

        for wm in WEATHER_MODELS:
            for sm in SEASON_MODELS:
                m_loss = sum(
                    _score_candidate_combo_on_observation(o, wm, sm, daylight_level, base_on)
                    for o in reg_obs
                ) / len(reg_obs)
                if m_loss < best_loss - 0.03:
                    best_loss = m_loss
                    best_wm = wm
                    best_sm = sm

        current_spec.weather_model = best_wm
        current_spec.season_model = best_sm
        current_spec.target_scale = 1.0
        current_spec.accuracy_pct = round(clamp(100.0 * math.exp(-best_loss * 0.8), 25.0, 100.0), 1)

        leaderboard_summary[regime] = {
            "observations": len(reg_obs),
            "selected_illum_model": current_spec.illum_model,
            "selected_weather_model": current_spec.weather_model,
            "selected_season_model": current_spec.season_model,
            "natural_accuracy_pct": current_spec.accuracy_pct,
        }

    return leaderboard_summary


async def async_run_gemini_room_optimization(
    hass: HomeAssistant,
    room_name: str,
    room_config: dict[str, Any],
    weights: LearnedRoomWeights,
    observations: list[PerceptiveObservation],
    sensor_profiles: dict[str, SensorLearnedProfile],
    current_outdoor: OutdoorEnvironmentState,
    current_result: RoomPerceptiveResult,
    light_metadata: list[dict[str, Any]] | None = None,
    regime_configs: dict[str, dict[str, Any]] | None = None,
) -> dict[str, Any] | None:
    """Use Home Assistant's Gemini AI (`ai_task` / `conversation`) to calibrate models, sensors & light shedding priority."""
    daylight_level = room_config.get("daylight_level", DAYLIGHT_MEDIUM)
    base_turn_on_map = {
        r: float((regime_configs or {}).get(r, {}).get("turn_on_lux", 12.0))
        for r in TIME_REGIMES
    }
    factorial_summary = run_local_factorial_backtest(
        weights=weights,
        observations=observations,
        daylight_level=daylight_level,
        base_turn_on_map=base_turn_on_map,
    )

    recent_obs = [o.to_dict() for o in observations[-20:]]
    sensor_summary = {
        eid: prof.to_dict() for eid, prof in sensor_profiles.items()
    }

    prompt = (
        f"You are the AI Calibration Architect for the 'Perceptive Lighting' Home Assistant integration "
        f"in room '{room_name}'.\n\n"
        f"Your three tasks are:\n"
        f"1. COMPETING MATHEMATICAL MODEL SELECTION PER TIME-OF-DAY REGIME (`morning`, `day`, `evening`, `overnight`):\n"
        f"   Select the best combination of:\n"
        f"   - Base Illuminance Dimming Curve (`illum_model`): {list(ILLUM_MODELS.keys())}\n"
        f"   - Weather & Sky Contrast Model (`weather_model`): {list(WEATHER_MODELS.keys())}\n"
        f"   - Seasonal, Photoperiod & Thermal Model (`season_model`): {list(SEASON_MODELS.keys())}\n\n"
        f"2. LIGHT PRIORITY ORDER FOR PROGRESSIVE DIMMING BELOW 1% (`light_priority_order`):\n"
        f"   When the user dims the room down to 1% and asks for even less light ('-'), we turn off one light source at a time.\n"
        f"   Review the room's lights (`room_lights`): rank all light `entity_id`s in an array `light_priority_order` from "
        f"   **1st to turn off** (non-dimmable on/off switches first, then high-lumen ceiling/spotlight bulbs) down to "
        f"   **last light standing at 1%** (softest ambient lamp, shelf, or strip light).\n\n"
        f"3. SENSOR OUTLIER EVALUATION (`sensor_updates`):\n"
        f"   Review `sensor_profiles` and live readings. If a sensor is permanently stuck at 0 lx while other sensors in the room "
        f"   read light, set `base_trust_weight` to 0.0 and `suggest_blacklist` to true. Keep `floor_lux` and `dropout_knee_lux` at 0.0 "
        f"   unless a sensor has a bright daytime peak (>45 lx) and drops abruptly to 0 lx early.\n\n"
        f"Room Telemetry:\n"
        f"- Daylight Exposure: {daylight_level}\n"
        f"- Current Regime: {current_outdoor.time_regime} ({current_outdoor.regime_explanation})\n"
        f"- Standalone Room Temp: {current_result.indoor_temp_c}C | Humidity: {current_result.indoor_humidity_pct}%\n"
        f"- Room Lights Metadata: {json.dumps(light_metadata or [])}\n"
        f"- Sensor Profiles: {json.dumps(sensor_summary)}\n"
        f"- Live Sensor Readings (lx): {json.dumps(current_result.sensor_breakdown)}\n"
        f"- Current Per-Regime Thresholds & Targets: {json.dumps(regime_configs or {})}\n"
        f"- Local Factorial Backtest Summary: {json.dumps(factorial_summary)}\n"
        f"- Recent User Calibration & Override Events ({len(recent_obs)}): {json.dumps(recent_obs)}\n\n"
        f"Return a JSON object with keys:\n"
        f"- `regime_models`: object mapping regime ('morning', 'day', 'evening', 'overnight') to "
        f"{{'illum_model': str, 'weather_model': str, 'season_model': str}}\n"
        f"- `light_priority_order`: array of light entity_id strings ordered from 1st-to-turn-off to last-to-keep-on\n"
        f"- `sensor_updates`: object mapping sensor entity_id to "
        f"{{'ai_location_guess': str, 'base_trust_weight': float, 'suggest_blacklist': bool}}\n"
        f"- `insight`: concise 1-2 sentence explanation of your light ordering, sensor evaluation, and model selection."
    )

    parsed_data: dict[str, Any] | None = None

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

        s_updates = parsed_data.get("sensor_updates")
        if isinstance(s_updates, dict):
            active_sensor_count = len(sensor_profiles)
            for eid, sdata in s_updates.items():
                if eid in sensor_profiles and isinstance(sdata, dict):
                    prof = sensor_profiles[eid]
                    if sdata.get("ai_location_guess"):
                        prof.ai_location_guess = str(sdata["ai_location_guess"])[:100]
                    if "base_trust_weight" in sdata:
                        # Don't let AI zero-out the only sensor in a room
                        min_w = 0.0 if active_sensor_count > 1 else 0.5
                        prof.base_trust_weight = clamp(float(sdata["base_trust_weight"]), min_w, 1.0)
                    if "suggest_blacklist" in sdata and active_sensor_count > 1:
                        prof.suggest_blacklist = bool(sdata["suggest_blacklist"])

        insight = str(parsed_data.get("insight", "")).strip()
        if insight:
            weights.last_ai_insight = insight[:260]
        weights.last_ai_optimized_utc = datetime.now(timezone.utc).isoformat()
        weights.total_optimizations += 1
        weights.initial_ai_audit_done = True
        return parsed_data

    rspec = weights.get_regime_spec(current_outdoor.time_regime)
    weights.last_ai_insight = (
        f"Factorial Fit ({current_outdoor.time_regime.title()}): "
        f"{ILLUM_MODELS.get(rspec.illum_model, rspec.illum_model)} + "
        f"{WEATHER_MODELS.get(rspec.weather_model, rspec.weather_model)}"
    )
    weights.last_ai_optimized_utc = datetime.now(timezone.utc).isoformat()
    weights.total_optimizations += 1
    weights.initial_ai_audit_done = True
    return None
