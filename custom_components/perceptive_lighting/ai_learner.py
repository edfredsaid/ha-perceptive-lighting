"""Online Machine Learning & Gemini AI Optimization for Perceptive Lighting.

Architecture:
1. Event-Driven Observation Journal:
   Whenever a user manually adjusts a room light, triggers a calibration button
   ('Feels Dark Now', 'Feels Bright Enough Now'), or nudges the feel ('Too Bright',
   'Needs More Light'), we record a rich contextual observation vector:
   - Indoor raw lux, natural spatial lux, and internal sensor contrast
   - Outdoor effective sky lux, cloud cover ratio, diffuse sky factor, solar elevation/azimuth
   - Active room activity ('relaxing', 'working', 'film', etc.)
   - What the model predicted vs. what the user actually wanted (desired light state/brightness)

2. Local Online Machine Learning (Contextual Weighted Regression):
   Immediately updates the room's multi-dimensional perceptual weights (no cloud latency):
   - `learned_base_lux_offset`: Shifts the baseline comfort lux for the active activity
   - `learned_adaptation_alpha_scale`: Adjusts how strongly outdoor brightness ('Cave Effect')
     pushes up the indoor target (learned specifically when outdoor lux is high)
   - `learned_diffuse_sensitivity`: Adjusts overcast vs. clear-sky sensitivity
   - `learned_night_dimming_scale`: Adjusts evening/nighttime target lux

3. Periodic & On-Demand Gemini AI Model Tuning (`ai_task.generate_data` / `conversation.process`):
   Uses Home Assistant's native Google Generative AI (`ai_task.google_ai_task` or
   `conversation.google_generative_ai`) to analyze the room's observation journal,
   identify contextual patterns (e.g., 'User consistently turns lights off on overcast
   mornings when indoor lux > 18 lx, meaning outdoor adaptation alpha is too aggressive'),
   and output structured parameter adjustments + a human-readable insight explanation!
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

from .engine import OutdoorEnvironmentState, RoomPerceptiveResult, clamp

_LOGGER = logging.getLogger(__name__)

MAX_OBSERVATION_HISTORY = 60


@dataclass
class PerceptiveObservation:
    """A single recorded human interaction or calibration event in a room."""

    timestamp_utc: str
    event_type: str  # "manual_light_on", "manual_light_off", "manual_dim", "feels_dark_now", "feels_bright_now", "nudge_lighter", "nudge_darker"
    activity: str
    indoor_raw_lux: float
    natural_spatial_lux: float
    outdoor_sky_lux: float
    cloud_cover_ratio: float
    diffuse_sky_factor: float
    solar_elevation: float
    solar_azimuth: float
    is_daytime: bool
    model_target_lux: float
    model_deficit_pct: float
    model_recommended_bri_pct: int
    user_desired_lights_on: bool
    user_desired_bri_pct: int

    def to_dict(self) -> dict[str, Any]:
        return {
            "timestamp_utc": self.timestamp_utc,
            "event_type": self.event_type,
            "activity": self.activity,
            "indoor_raw_lux": round(self.indoor_raw_lux, 1),
            "natural_spatial_lux": round(self.natural_spatial_lux, 1),
            "outdoor_sky_lux": round(self.outdoor_sky_lux, 1),
            "cloud_cover_ratio": round(self.cloud_cover_ratio, 2),
            "diffuse_sky_factor": round(self.diffuse_sky_factor, 2),
            "solar_elevation": round(self.solar_elevation, 1),
            "solar_azimuth": round(self.solar_azimuth, 1),
            "is_daytime": self.is_daytime,
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
            event_type=str(data.get("event_type", "manual")),
            activity=str(data.get("activity", "relaxing")),
            indoor_raw_lux=float(data.get("indoor_raw_lux", 0.0)),
            natural_spatial_lux=float(data.get("natural_spatial_lux", 0.0)),
            outdoor_sky_lux=float(data.get("outdoor_sky_lux", 0.0)),
            cloud_cover_ratio=float(data.get("cloud_cover_ratio", 0.5)),
            diffuse_sky_factor=float(data.get("diffuse_sky_factor", 1.0)),
            solar_elevation=float(data.get("solar_elevation", 0.0)),
            solar_azimuth=float(data.get("solar_azimuth", 180.0)),
            is_daytime=bool(data.get("is_daytime", True)),
            model_target_lux=float(data.get("model_target_lux", 65.0)),
            model_deficit_pct=float(data.get("model_deficit_pct", 50.0)),
            model_recommended_bri_pct=int(data.get("model_recommended_bri_pct", 50)),
            user_desired_lights_on=bool(data.get("user_desired_lights_on", True)),
            user_desired_bri_pct=int(data.get("user_desired_bri_pct", 50)),
        )


@dataclass
class LearnedRoomWeights:
    """Multi-dimensional learned perceptual parameters for a room."""

    # Per-activity base target lux multiplier (e.g. {"relaxing": 0.72, "working": 1.15})
    activity_target_multipliers: dict[str, float] = field(default_factory=dict)
    # Scales how strongly outdoor sky lux raises indoor target ("The Cave Effect", 0.20 to 2.50)
    outdoor_adaptation_scale: float = 1.0
    # Scales diffuse overcast sky bonus (0.70 to 1.40)
    diffuse_sky_sensitivity: float = 1.0
    # Scales evening/night target lux (0.30 to 2.00)
    night_comfort_scale: float = 1.0
    # Scales overall recommended brightness output (0.40 to 1.50)
    brightness_output_scale: float = 1.0
    # Latest human-readable insight from local ML or Gemini AI
    last_ai_insight: str = "Learning from your room interactions and environmental history."
    last_ai_optimized_utc: str | None = None
    total_optimizations: int = 0

    def get_activity_multiplier(self, activity: str) -> float:
        return float(self.activity_target_multipliers.get(activity, 1.0))

    def to_dict(self) -> dict[str, Any]:
        return {
            "activity_target_multipliers": {
                k: round(v, 3) for k, v in self.activity_target_multipliers.items()
            },
            "outdoor_adaptation_scale": round(self.outdoor_adaptation_scale, 3),
            "diffuse_sky_sensitivity": round(self.diffuse_sky_sensitivity, 3),
            "night_comfort_scale": round(self.night_comfort_scale, 3),
            "brightness_output_scale": round(self.brightness_output_scale, 3),
            "last_ai_insight": self.last_ai_insight,
            "last_ai_optimized_utc": self.last_ai_optimized_utc,
            "total_optimizations": self.total_optimizations,
        }

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> LearnedRoomWeights:
        if not isinstance(data, dict):
            return cls()
        return cls(
            activity_target_multipliers={
                str(k): float(v)
                for k, v in data.get("activity_target_multipliers", {}).items()
            },
            outdoor_adaptation_scale=float(data.get("outdoor_adaptation_scale", 1.0)),
            diffuse_sky_sensitivity=float(data.get("diffuse_sky_sensitivity", 1.0)),
            night_comfort_scale=float(data.get("night_comfort_scale", 1.0)),
            brightness_output_scale=float(data.get("brightness_output_scale", 1.0)),
            last_ai_insight=str(
                data.get(
                    "last_ai_insight",
                    "Learning from your room interactions and environmental history.",
                )
            ),
            last_ai_optimized_utc=data.get("last_ai_optimized_utc"),
            total_optimizations=int(data.get("total_optimizations", 0)),
        )


def apply_online_sgd_step(
    weights: LearnedRoomWeights,
    obs: PerceptiveObservation,
    learning_rate: float = 0.18,
) -> None:
    """Perform an immediate contextual gradient step on the room's perceptual weights.

    Unlike a blunt 1D offset, this attributes the error across the active environmental features:
    - If outdoor lux is high when the user says 'Bright Enough Now', it reduces `outdoor_adaptation_scale`
      (because the 'Cave Effect' boost was too aggressive).
    - If it's overcast/cloudy when the user says 'Bright Enough Now', it increases `diffuse_sky_sensitivity`
      (recognizing that diffuse light fills this room better than raw lux suggests).
    - Updates the specific `activity_target_multipliers[obs.activity]` rather than polluting other activities.
    """
    act = obs.activity
    cur_act_mult = weights.get_activity_multiplier(act)

    # Determine direction & magnitude of perceptual error:
    # Positive error_signal -> user needed MORE light (model underestimated target lux)
    # Negative error_signal -> user wanted LESS/NO light (model overestimated target lux)
    if obs.event_type in ("feels_bright_now", "manual_light_off"):
        # User explicitly wants lights OFF at current natural_spatial_lux.
        # For deficit to be 0%, target_comfort_lux needs to be <= natural_spatial_lux * 0.72.
        desired_target = max(2.0, obs.natural_spatial_lux * 0.68)
        ratio = desired_target / max(2.0, obs.model_target_lux)
        error_signal = clamp(math.log2(max(0.15, ratio)), -1.5, -0.15)
    elif obs.event_type in ("feels_dark_now", "manual_light_on"):
        # User explicitly wants lights ON at current natural_spatial_lux.
        desired_target = max(obs.model_target_lux * 1.35, obs.natural_spatial_lux * 2.2, 30.0)
        ratio = desired_target / max(2.0, obs.model_target_lux)
        error_signal = clamp(math.log2(max(1.15, ratio)), 0.15, 1.5)
    elif obs.event_type == "nudge_darker":
        error_signal = -0.35
    elif obs.event_type == "nudge_lighter":
        error_signal = 0.35
    else:
        # Manual brightness dim/brighten adjustment while lights are ON
        bri_diff = (obs.user_desired_bri_pct - obs.model_recommended_bri_pct) / 100.0
        error_signal = clamp(bri_diff * 1.2, -1.0, 1.0)
        weights.brightness_output_scale = clamp(
            weights.brightness_output_scale * (1.0 + learning_rate * bri_diff * 0.5),
            0.35,
            1.60,
        )

    # Feature attribution weights (how much each environmental factor is active right now)
    has_outdoor_contrast = obs.is_daytime and obs.outdoor_sky_lux > 300.0
    is_overcast_diffuse = obs.is_daytime and obs.cloud_cover_ratio >= 0.65

    # 1. Update activity-specific base target multiplier
    act_step = learning_rate * error_signal * (0.55 if has_outdoor_contrast else 0.85)
    new_act_mult = clamp(cur_act_mult * math.pow(2.0, act_step), 0.15, 4.0)
    weights.activity_target_multipliers[act] = new_act_mult

    # 2. If daytime with significant outdoor brightness, attribute part of the error to Outdoor Adaptation ("Cave Effect")
    if has_outdoor_contrast:
        adapt_step = learning_rate * error_signal * 0.65
        weights.outdoor_adaptation_scale = clamp(
            weights.outdoor_adaptation_scale * math.pow(2.0, adapt_step),
            0.15,
            2.50,
        )

    # 3. If overcast/diffuse daytime, adjust diffuse sky sensitivity
    #    (If user says 'too bright / lights off' on a cloudy day, diffuse light is carrying further than expected -> boost diffuse sensitivity)
    if is_overcast_diffuse:
        diff_step = -learning_rate * error_signal * 0.25
        weights.diffuse_sky_sensitivity = clamp(
            weights.diffuse_sky_sensitivity * (1.0 + diff_step),
            0.75,
            1.45,
        )

    # 4. If nighttime/twilight, update night comfort scale
    if not obs.is_daytime:
        night_step = learning_rate * error_signal * 0.60
        weights.night_comfort_scale = clamp(
            weights.night_comfort_scale * math.pow(2.0, night_step),
            0.25,
            2.20,
        )

    weights.total_optimizations += 1
    weights.last_ai_insight = (
        f"Online ML updated '{act}' profile (x{new_act_mult:.2f}) & "
        f"outdoor adaptation (x{weights.outdoor_adaptation_scale:.2f}) from {obs.event_type}."
    )


async def async_run_gemini_room_optimization(
    hass: HomeAssistant,
    room_name: str,
    room_config: dict[str, Any],
    weights: LearnedRoomWeights,
    observations: list[PerceptiveObservation],
    current_outdoor: OutdoorEnvironmentState,
    current_result: RoomPerceptiveResult,
) -> bool:
    """Call Home Assistant's Gemini AI (`ai_task` or `conversation`) to analyze room observations and refine model weights."""
    # Find available AI Task or Conversation entity
    ai_task_entities = hass.states.async_entity_ids("ai_task")
    conv_entities = [
        eid
        for eid in hass.states.async_entity_ids("conversation")
        if "google" in eid or "gemini" in eid or "openai" in eid or "llm" in eid
    ]

    if not ai_task_entities and not conv_entities:
        _LOGGER.debug(
            "[%s] No AI Task or LLM Conversation entity found; relying on local Online ML.",
            room_name,
        )
        return False

    recent_obs = [o.to_dict() for o in observations[-25:]]
    prompt_context = {
        "room_name": room_name,
        "daylight_exposure": room_config.get("daylight_level", "medium"),
        "current_weights": weights.to_dict(),
        "current_state": {
            "raw_lux": current_result.raw_lux,
            "natural_spatial_lux": current_result.natural_spatial_lux,
            "target_comfort_lux": current_result.target_comfort_lux,
            "perceptive_illuminance_pct": current_result.perceptive_illuminance_pct,
            "lighting_deficit_pct": current_result.lighting_deficit_pct,
            "outdoor_sky_lux": current_outdoor.effective_sky_lux,
            "weather_condition": current_outdoor.weather_condition,
            "cloud_cover_ratio": current_outdoor.cloud_cover_ratio,
            "solar_elevation": current_outdoor.solar_elevation,
        },
        "user_observation_history": recent_obs,
    }

    instructions = (
        "You are the Psychophysical & Architectural Lighting Optimization Engine for a smart home room. "
        "Analyze the room's `current_weights`, `current_state`, and `user_observation_history` "
        "(which records every time the user manually turned lights on/off, dimmed them, or pressed "
        "'feels_dark_now' / 'feels_bright_now' / 'nudge_darker' / 'nudge_lighter').\n\n"
        "Understand what the parameters do:\n"
        "- `activity_multiplier`: Multiplies base target lux for the current activity (0.15 to 3.5). Lower = room feels bright enough at lower lux so lights stay off longer.\n"
        "- `outdoor_adaptation_scale`: Scales how much bright outdoor daylight ('The Cave Effect') raises the required indoor target lux (0.15 to 2.5). If the user says the room is bright enough during the day despite low indoor lux, lower this value.\n"
        "- `diffuse_sky_sensitivity`: Scales how much overcast/diffuse daylight boosts perceived indoor brightness (0.75 to 1.45).\n"
        "- `night_comfort_scale`: Scales evening/night target lux (0.25 to 2.0).\n"
        "- `brightness_output_scale`: Scales recommended bulb brightness % when lights turn on (0.35 to 1.5).\n\n"
        f"Room Telemetry & Observations JSON:\n{json.dumps(prompt_context)}\n\n"
        "Return ONLY valid JSON with keys: "
        "`activity_multiplier` (number), `outdoor_adaptation_scale` (number), "
        "`diffuse_sky_sensitivity` (number), `night_comfort_scale` (number), "
        "`brightness_output_scale` (number), and `insight` (a concise 1-2 sentence explanation of what you learned and adjusted)."
    )

    parsed_data: dict[str, Any] | None = None

    # Prefer `ai_task.generate_data` with structured schema if available
    if ai_task_entities and hass.services.has_service("ai_task", "generate_data"):
        ai_entity = (
            "ai_task.google_ai_task"
            if "ai_task.google_ai_task" in ai_task_entities
            else ai_task_entities[0]
        )
        try:
            response = await hass.services.async_call(
                "ai_task",
                "generate_data",
                {
                    "entity_id": ai_entity,
                    "task_name": f"Optimize {room_name} Perceptive Lighting",
                    "instructions": instructions,
                    "structure": {
                        "activity_multiplier": {
                            "description": "Target lux multiplier for the active activity (0.15 to 3.5)",
                            "required": True,
                            "selector": {"number": {"min": 0.15, "max": 3.5, "step": 0.05}},
                        },
                        "outdoor_adaptation_scale": {
                            "description": "Outdoor Cave-Effect adaptation scale (0.15 to 2.5)",
                            "required": True,
                            "selector": {"number": {"min": 0.15, "max": 2.5, "step": 0.05}},
                        },
                        "diffuse_sky_sensitivity": {
                            "description": "Diffuse overcast sky multiplier (0.75 to 1.45)",
                            "required": True,
                            "selector": {"number": {"min": 0.75, "max": 1.45, "step": 0.05}},
                        },
                        "night_comfort_scale": {
                            "description": "Nighttime target lux scale (0.25 to 2.0)",
                            "required": True,
                            "selector": {"number": {"min": 0.25, "max": 2.0, "step": 0.05}},
                        },
                        "brightness_output_scale": {
                            "description": "Bulb brightness output scale (0.35 to 1.5)",
                            "required": True,
                            "selector": {"number": {"min": 0.35, "max": 1.5, "step": 0.05}},
                        },
                        "insight": {
                            "description": "Concise explanation of learned perceptual adjustments",
                            "required": True,
                            "selector": {"text": {}},
                        },
                    },
                },
                blocking=True,
                return_response=True,
            )
            if isinstance(response, dict):
                data_block = response.get("data", response)
                if isinstance(data_block, dict) and "outdoor_adaptation_scale" in data_block:
                    parsed_data = data_block
                elif isinstance(data_block, str):
                    parsed_data = _extract_json_dict(data_block)
        except Exception as err:  # noqa: BLE001
            _LOGGER.debug("[%s] ai_task.generate_data call failed: %s", room_name, err)

    # Fallback to `conversation.process` with Google Generative AI if needed
    if parsed_data is None and conv_entities:
        try:
            conv_res = await hass.services.async_call(
                "conversation",
                "process",
                {
                    "agent_id": conv_entities[0],
                    "text": instructions,
                },
                blocking=True,
                return_response=True,
            )
            if isinstance(conv_res, dict):
                speech = (
                    conv_res.get("response", {})
                    .get("speech", {})
                    .get("plain", {})
                    .get("speech", "")
                )
                parsed_data = _extract_json_dict(speech)
        except Exception as err:  # noqa: BLE001
            _LOGGER.debug("[%s] conversation.process call failed: %s", room_name, err)

    if not parsed_data:
        return False

    # Smoothly blend Gemini's recommended weights with current weights (70% Gemini + 30% prior for stability)
    act = current_result.active_activity
    cur_act = weights.get_activity_multiplier(act)
    if "activity_multiplier" in parsed_data:
        ai_act = clamp(float(parsed_data["activity_multiplier"]), 0.15, 3.5)
        weights.activity_target_multipliers[act] = round(0.7 * ai_act + 0.3 * cur_act, 3)

    if "outdoor_adaptation_scale" in parsed_data:
        ai_adapt = clamp(float(parsed_data["outdoor_adaptation_scale"]), 0.15, 2.5)
        weights.outdoor_adaptation_scale = round(
            0.7 * ai_adapt + 0.3 * weights.outdoor_adaptation_scale, 3
        )

    if "diffuse_sky_sensitivity" in parsed_data:
        ai_diff = clamp(float(parsed_data["diffuse_sky_sensitivity"]), 0.75, 1.45)
        weights.diffuse_sky_sensitivity = round(
            0.7 * ai_diff + 0.3 * weights.diffuse_sky_sensitivity, 3
        )

    if "night_comfort_scale" in parsed_data:
        ai_night = clamp(float(parsed_data["night_comfort_scale"]), 0.25, 2.0)
        weights.night_comfort_scale = round(
            0.7 * ai_night + 0.3 * weights.night_comfort_scale, 3
        )

    if "brightness_output_scale" in parsed_data:
        ai_bri = clamp(float(parsed_data["brightness_output_scale"]), 0.35, 1.5)
        weights.brightness_output_scale = round(
            0.7 * ai_bri + 0.3 * weights.brightness_output_scale, 3
        )

    if parsed_data.get("insight"):
        weights.last_ai_insight = f"✨ Gemini AI: {parsed_data['insight']}"

    weights.last_ai_optimized_utc = datetime.now(timezone.utc).isoformat()
    weights.total_optimizations += 1
    _LOGGER.info(
        "[%s] Gemini AI optimized Perceptive Lighting weights: %s",
        room_name,
        weights.to_dict(),
    )
    return True


def _extract_json_dict(text: str) -> dict[str, Any] | None:
    """Extract a JSON object from an LLM text response."""
    if not text:
        return None
    start = text.find("{")
    end = text.rfind("}")
    if start == -1 or end == -1 or end <= start:
        return None
    try:
        return json.loads(text[start : end + 1])
    except Exception:  # noqa: BLE001
        return None
