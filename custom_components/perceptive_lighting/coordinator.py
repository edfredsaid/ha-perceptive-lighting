"""Central Hub and Room Controllers for Perceptive Lighting (v2.0.0).

Implements:
1. Closed-Loop Measured `Total Room Lux` Sense-Check Controller.
2. True `1%` Minimum Brightness & Progressive One-by-One Light Shedding (`light_priority_order`).
3. Smooth Continuous Regime Blending (`Morning`, `Day`, `Evening`, `Overnight`) with Configurable Schedule & Factors.
4. Per-Regime Default Scenes, Off-State Scene Overrides (Scene A vs Scene B), and Per-Regime Entity Triggers.
5. Direct Control + 5-Second Settled Lux Measurement & 4-Second Multi-Bulb Manual Override Debouncing.
6. Room & Global Reset Mechanism (`async_reset_room`, `async_reset_all_rooms`).
"""
from __future__ import annotations

import asyncio
from copy import deepcopy
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
import logging
import math
from typing import Any

from homeassistant.config_entries import ConfigEntry
from homeassistant.const import (
    ATTR_ENTITY_ID,
    EVENT_STATE_CHANGED,
    STATE_OFF,
    STATE_ON,
    STATE_PLAYING,
    STATE_UNAVAILABLE,
    STATE_UNKNOWN,
)
from homeassistant.core import Context, Event, HomeAssistant, callback
from homeassistant.helpers import (
    area_registry as ar,
    device_registry as dr,
    entity_registry as er,
)
from homeassistant.helpers.dispatcher import async_dispatcher_send
from homeassistant.helpers.event import async_call_later, async_track_time_interval
from homeassistant.helpers.storage import Store
from homeassistant.util import dt as dt_util

from .ai_learner import (
     LearnedRoomWeights,
    MAX_OBSERVATION_HISTORY,
    OBSERVATIONS_BEFORE_AUTO_AI_CALL,
    PerceptiveObservation,
    async_run_gemini_room_optimization,
    run_local_factorial_backtest,
)
from .const import (
    ACTIVITY_DEFINITIONS,
    ACTIVITY_RELAXING,
    ACTIVITY_TRANSIT,
    CCT_EXTERNAL,
    CCT_KRUITHOF,
    CONF_ACTIVITIES,
    CONF_ACTIVITY_TRIGGERS,
    CONF_ADAPTIVE_SCENES,
    CONF_ADJUST_INTERVAL_MINUTES,
    CONF_AREA_ID,
    CONF_BLOCKER_ENTITIES,
    CONF_CCT_MODE,
    CONF_DAYLIGHT_LEVEL,
    CONF_DEFAULT_ACTIVITY,
    CONF_EXCLUDED_ENTITIES,
    CONF_INCLUDE_MEDIA_PLAYERS,
    CONF_LIGHT_GROUPS,
    CONF_LIGHT_ORDER_LOCKED,
    CONF_LIGHT_PRIORITY_ORDER,
    CONF_LINKED_HA_SCENES,
    CONF_ON_OFF_COOLDOWN_MINUTES,
    CONF_OPERATING_MODE,
    CONF_OUTDOOR_SENSORS,
    CONF_REGIME_CONFIGS,
    CONF_REGIME_SCHEDULE,
    CONF_ROOM_ID,
    CONF_ROOM_NAME,
    CONF_ROOMS,
    CONF_SCENE_TRIGGERS,
    CONF_SENSOR_PROFILES,
    CONF_SUN_ENTITY,
    CONF_SUN_WINDOW_DIRECTION,
    CONF_TRANSITION_SECONDS,
    CONF_WEATHER_ENTITY,
    CONF_WINDOW_AZIMUTH,
    DAYLIGHT_MEDIUM,
    DEFAULT_ADJUST_INTERVAL_MINUTES,
    DEFAULT_MICRO_NUDGE_STEP_PCT,
    DEFAULT_ON_OFF_COOLDOWN_MINUTES,
    DEFAULT_REGIME_SCHEDULE,
    DEFAULT_REGIME_SETTINGS,
    DEFAULT_SUBSTANTIAL_LUX_SHIFT_PCT,
    DEFAULT_TRANSITION_SECONDS,
    DOMAIN,
    MODE_FULL_ADAPTIVE,
    MODE_SENSOR_ONLY,
    MODE_SIMPLE_TRANSIT,
    REGIME_DAY,
    REGIME_EVENING,
    REGIME_MORNING,
    REGIME_OVERNIGHT,
    SUN_DIR_AUTO,
    SUN_DIR_COMPASS,
    TIME_REGIMES,
)
from .engine import (
    OutdoorEnvironmentState,
    RoomPerceptiveResult,
    SensorLearnedProfile,
    clamp,
    compute_outdoor_environment,
    compute_room_perceptive_illuminance,
    estimate_day_length_hours,
)
from .history_learner import async_learn_sensor_profiles_from_history

_LOGGER = logging.getLogger(__name__)

STORAGE_VERSION = 1
STORAGE_KEY = f"{DOMAIN}.runtime_state"

SIGNAL_HUB_UPDATED = f"{DOMAIN}_hub_updated"
SIGNAL_ROOM_UPDATED = f"{DOMAIN}_room_updated"


@dataclass
class AdaptiveSceneSpec:
    """Represents a lighting scene (either linked to a native HA scene or captured from room lights)."""

    scene_id: str
    name: str
    activity: str = ACTIVITY_RELAXING
    reference_deficit_pct: float = 65.0
    linked_ha_scene_entity: str | None = None
    light_states: dict[str, dict[str, Any]] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        return {
            "scene_id": self.scene_id,
            "name": self.name,
            "activity": self.activity,
            "reference_deficit_pct": round(self.reference_deficit_pct, 1),
            "linked_ha_scene_entity": self.linked_ha_scene_entity,
            "light_states": self.light_states,
        }

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> AdaptiveSceneSpec:
        return cls(
            scene_id=data["scene_id"],
            name=data.get("name", data["scene_id"]),
            activity=data.get("activity", ACTIVITY_RELAXING),
            reference_deficit_pct=float(data.get("reference_deficit_pct", 65.0)),
            linked_ha_scene_entity=data.get("linked_ha_scene_entity"),
            light_states=data.get("light_states", {}),
        )


def _classify_light_entity(
    eid: str,
    friendly_name: str,
    model_str: str,
    supported_color_modes: list[str] | tuple[str, ...],
) -> dict[str, Any]:
    """Classify a light entity's capabilities and default shedding priority rank (1=shed 1st, 5=keep last)."""
    modes = [str(m).lower() for m in (supported_color_modes or [])]
    dimmable_modes = {"brightness", "color_temp", "hs", "xy", "rgb", "rgbw", "rgbww"}
    is_dimmable = any(m in dimmable_modes for m in modes) or (not modes)
    if modes == ["onoff"]:
        is_dimmable = False
    supports_cct = "color_temp" in modes

    combined = f"{eid} {friendly_name} {model_str}".lower()

    if not is_dimmable:
        category = "onoff_switch"
        label = "On/Off Switch (Shed 1st)"
        rank = 1
    elif any(k in combined for k in ("1000lm", "800lm", "ceiling", "main", "room_light", "pendant")):
        category = "high_output"
        label = "High-Output / Main Light"
        rank = 2
    elif any(k in combined for k in ("spot", "gu10", "575lm", "downlight")):
        category = "spotlight"
        label = "Spotlight / Downlight"
        rank = 3
    elif any(k in combined for k in ("strip", "shelf", "shelves", "counter", "under_stairs", "bookshelf", "tv_lamp", "wreath", "cabinet")):
        category = "accent"
        label = "Accent / Strip / Shelf (Keep Last)"
        rank = 5
    else:
        category = "lamp"
        label = "Lamp / Standard Fixture"
        rank = 4

    return {
        "entity_id": eid,
        "friendly_name": friendly_name or eid,
        "model": model_str,
        "is_dimmable": is_dimmable,
        "supports_cct": supports_cct,
        "category": category,
        "category_label": label,
        "heuristic_rank": rank,
    }


class RoomController:
    """Manages Perceptive Illuminance, progressive light shedding, scenes, and closed-loop learning for one room."""

    def __init__(
        self,
        hub: PerceptiveLightingHub,
        room_config: dict[str, Any],
        persisted_state: dict[str, Any] | None = None,
    ) -> None:
        """Initialize the room controller."""
        self.hub = hub
        self.hass = hub.hass
        self.config = dict(room_config)
        self.room_id: str = room_config[CONF_ROOM_ID]
        self.room_name: str = room_config.get(CONF_ROOM_NAME, self.room_id)
        self.area_id: str | None = room_config.get(CONF_AREA_ID)
        self.operating_mode: str = room_config.get(
            CONF_OPERATING_MODE, MODE_FULL_ADAPTIVE
        )

        # Auto-discovered area entity lists
        self.lux_sensors: list[str] = []
        self.presence_sensors: list[str] = []
        self.media_players: list[str] = []
        self.lights: list[str] = []
        self.temp_sensors: list[str] = []
        self.humidity_sensors: list[str] = []
        self.auto_excluded_helpers: list[str] = []
        self.all_area_entities: dict[str, list[str]] = {
            "illuminance_sensors": [],
            "presence_sensors": [],
            "media_players": [],
            "lights": [],
            "temperature_sensors": [],
            "humidity_sensors": [],
        }
        self.light_metadata: dict[str, dict[str, Any]] = {}

        # Restore persisted state (with automatic v2.0.0 cleanup of legacy v1.3.x corrupted weights)
        pstate = persisted_state if isinstance(persisted_state, dict) else {}
        is_v200_clean = bool(pstate.get("schema_v200_cleaned", False))

        self.automation_locked: bool = bool(pstate.get("automation_locked", False))
        self.window_azimuth: float | None = pstate.get(
            "window_azimuth", room_config.get(CONF_WINDOW_AZIMUTH)
        )

        default_act = (
            ACTIVITY_TRANSIT
            if self.operating_mode == MODE_SIMPLE_TRANSIT
            else room_config.get(CONF_DEFAULT_ACTIVITY, ACTIVITY_RELAXING)
        )
        self.active_activity: str = (
            pstate.get("active_activity", default_act) if is_v200_clean else default_act
        )
        self.active_scene_id: str = pstate.get("active_scene_id", "default")
        self.manual_scene_override_id: str | None = None

        # Per-regime configurations (occupied_scene, off_scene, turn_on_lux, target_brightness_pct, target_total_lux, shed_count)
        self.regime_configs: dict[str, dict[str, Any]] = deepcopy(DEFAULT_REGIME_SETTINGS)
        raw_rcfgs = pstate.get(CONF_REGIME_CONFIGS, room_config.get(CONF_REGIME_CONFIGS, {}))
        if is_v200_clean and isinstance(raw_rcfgs, dict):
            for rkey in TIME_REGIMES:
                if isinstance(raw_rcfgs.get(rkey), dict):
                    self.regime_configs[rkey].update(raw_rcfgs[rkey])

        # Per-regime entity triggers
        raw_triggers = pstate.get(
            CONF_SCENE_TRIGGERS, room_config.get(CONF_SCENE_TRIGGERS, [])
        )
        self.scene_triggers: list[dict[str, Any]] = (
            [dict(t) for t in raw_triggers if isinstance(t, dict)]
            if isinstance(raw_triggers, list)
            else []
        )
        # Migrate legacy CONF_ACTIVITY_TRIGGERS if present and scene_triggers is empty
        if not self.scene_triggers and isinstance(room_config.get(CONF_ACTIVITY_TRIGGERS), dict):
            for idx, (teid, tact) in enumerate(room_config[CONF_ACTIVITY_TRIGGERS].items()):
                self.scene_triggers.append(
                    {
                        "id": f"migrated_{idx}",
                        "regime": "any",
                        "entity_id": teid,
                        "states": ["on", "playing"],
                        "scene_id": f"activity_{tact}",
                        "scale_dynamically": True,
                    }
                )

        # Light priority ordering for progressive 1% shedding
        self.light_priority_order: list[str] = list(
            pstate.get(
                CONF_LIGHT_PRIORITY_ORDER,
                room_config.get(CONF_LIGHT_PRIORITY_ORDER, []),
            )
        )
        self.light_order_locked: bool = bool(
            pstate.get(
                CONF_LIGHT_ORDER_LOCKED,
                room_config.get(CONF_LIGHT_ORDER_LOCKED, False),
            )
        )

        # Sensor profiles & learned weights
        self.sensor_profiles: dict[str, SensorLearnedProfile] = {}
        if is_v200_clean:
            for eid, pdata in pstate.get("sensor_profiles", {}).items():
                self.sensor_profiles[eid] = SensorLearnedProfile.from_dict(pdata)
            for eid, pdata in room_config.get(CONF_SENSOR_PROFILES, {}).items():
                if eid not in self.sensor_profiles and isinstance(pdata, dict):
                    self.sensor_profiles[eid] = SensorLearnedProfile.from_dict(pdata)

        self.learned_weights: LearnedRoomWeights = (
            LearnedRoomWeights.from_dict(pstate.get("learned_weights", {}))
            if is_v200_clean
            else LearnedRoomWeights()
        )
        self.observations: list[PerceptiveObservation] = (
            [
                PerceptiveObservation.from_dict(o)
                for o in pstate.get("observations", [])
                if isinstance(o, dict)
            ][-MAX_OBSERVATION_HISTORY:]
            if is_v200_clean
            else []
        )

        self.measured_max_light_delta_lux: float = float(
            pstate.get("measured_max_light_delta_lux", 3.5) if is_v200_clean else 3.5
        )
        self.feel_nudge_day_pct: float = 0.0
        self.feel_nudge_night_pct: float = 0.0
        self._obs_since_last_gemini_call: int = 0

        # Adaptive scenes (linked HA scenes + user captured scenes)
        self.adaptive_scenes: dict[str, AdaptiveSceneSpec] = {}
        for sid, sdata in pstate.get("adaptive_scenes", {}).items():
            if isinstance(sdata, dict):
                self.adaptive_scenes[sid] = AdaptiveSceneSpec.from_dict(sdata)
        for sid, sdata in room_config.get(CONF_ADAPTIVE_SCENES, {}).items():
            if sid not in self.adaptive_scenes and isinstance(sdata, dict):
                self.adaptive_scenes[sid] = AdaptiveSceneSpec.from_dict(sdata)

        # Live runtime state
        self.result: RoomPerceptiveResult = RoomPerceptiveResult(
            active_activity=self.active_activity
        )
        self.smoothed_indoor_lux: dict[str, float] = {}
        self.is_occupied: bool = False
        self.occupied_by_entities: list[str] = []
        self.manual_override_active: bool = False
        self.manual_override_baseline_natural_pct: float | None = None
        self.last_on_off_change_utc: datetime | None = None
        self.last_adjustment_utc: datetime | None = None
        self.controlled_lights_are_on: bool = False
        self.off_scene_currently_active: bool = False
        self.active_shed_count: int = 0
        self.shed_light_entities: list[str] = []
        self.pre_light_natural_lux: float | None = None
        self.resolved_scene_id: str = "all_lights"
        self.resolved_scene_reason: str = "Default Room Lights"
        self.decision_reason: str = "Initializing..."
        self._recent_context_ids: set[str] = set()
        self._ignore_light_events_until_utc: datetime | None = None
        self._settle_measure_unsub: Any = None
        self._manual_debounce_unsub: Any = None
        self._pending_manual_event_type: str = "manual_dim"

    @property
    def excluded_entities(self) -> set[str]:
        """Return set of user-blacklisted entity_ids for this room."""
        return set(self.config.get(CONF_EXCLUDED_ENTITIES, []))

    @property
    def cooldown_active(self) -> bool:
        """Return True if the on/off anti-flicker lockout is active (only applies while occupied)."""
        if self.last_on_off_change_utc is None:
            return False
        cooldown_mins = float(
            self.config.get(
                CONF_ON_OFF_COOLDOWN_MINUTES, DEFAULT_ON_OFF_COOLDOWN_MINUTES
            )
        )
        elapsed = (
            datetime.now(timezone.utc) - self.last_on_off_change_utc
        ).total_seconds()
        return elapsed < (cooldown_mins * 60.0)

    def to_persisted_dict(self) -> dict[str, Any]:
        """Serialize runtime state for persistent storage."""
        return {
            "schema_v200_cleaned": True,
            "automation_locked": self.automation_locked,
            "window_azimuth": self.window_azimuth,
            "active_activity": self.active_activity,
            "active_scene_id": self.active_scene_id,
            "regime_configs": deepcopy(self.regime_configs),
            "scene_triggers": deepcopy(self.scene_triggers),
            "light_priority_order": list(self.light_priority_order),
            "light_order_locked": self.light_order_locked,
            "measured_max_light_delta_lux": round(self.measured_max_light_delta_lux, 2),
            "learned_weights": self.learned_weights.to_dict(),
            "observations": [
                o.to_dict() for o in self.observations[-MAX_OBSERVATION_HISTORY:]
            ],
            "adaptive_scenes": {
                k: v.to_dict() for k, v in self.adaptive_scenes.items()
            },
            "sensor_profiles": {
                k: v.to_dict() for k, v in self.sensor_profiles.items()
            },
        }

    async def async_reset_room(self, relearn_history: bool = True) -> None:
        """Reset all learned thresholds, brightness targets, observations, and sensor profiles for this room."""
        self.regime_configs = deepcopy(DEFAULT_REGIME_SETTINGS)
        self.learned_weights = LearnedRoomWeights()
        self.observations = []
        self.sensor_profiles = {}
        self.measured_max_light_delta_lux = 3.5
        self.active_shed_count = 0
        self.shed_light_entities = []
        self.manual_override_active = False
        self.manual_override_baseline_natural_pct = None
        self.manual_scene_override_id = None
        self.last_on_off_change_utc = None
        self.last_adjustment_utc = None
        if not self.light_order_locked:
            self.light_priority_order = []

        self.async_discover_area_entities()
        if relearn_history and self.lux_sensors:
            self.sensor_profiles = await async_learn_sensor_profiles_from_history(
                self.hass,
                self.lux_sensors,
                self.sensor_profiles,
                days_back=10,
                is_outdoor=False,
            )
        self.async_evaluate_room(self.hub.outdoor_state, trigger_control=True)
        await self.hub.async_save_runtime_state()

    @callback
    def async_discover_area_entities(self) -> None:
        """Dynamically discover all relevant entities in the room's HA Area, excluding TRVs and duplicate helpers."""
        if not self.area_id:
            return

        ent_reg = er.async_get(self.hass)
        dev_reg = dr.async_get(self.hass)

        area_device_ids: set[str] = set()
        climate_or_trv_device_ids: set[str] = set()
        trv_keywords = ("trv", "radiator", "valve", "thermostat", "heating", "boiler")

        for dev in dev_reg.devices.values():
            if dev.area_id == self.area_id and not dev.disabled_by:
                area_device_ids.add(dev.id)
                dev_label = f"{dev.name or ''} {dev.name_by_user or ''} {dev.model or ''}".lower()
                if any(kw in dev_label for kw in trv_keywords):
                    climate_or_trv_device_ids.add(dev.id)

        for entry in ent_reg.entities.values():
            if entry.domain in ("climate", "water_heater") and entry.device_id:
                climate_or_trv_device_ids.add(entry.device_id)

        lux_list: list[str] = []
        physical_lux_list: list[str] = []
        helper_lux_list: list[str] = []
        pres_list: list[str] = []
        physical_pres_list: list[str] = []
        helper_pres_list: list[str] = []
        media_list: list[str] = []
        light_list: list[str] = []
        temp_list: list[str] = []
        hum_list: list[str] = []

        for entry in ent_reg.entities.values():
            if entry.disabled_by or entry.platform == DOMAIN:
                continue

            in_area = entry.area_id == self.area_id or (
                entry.area_id is None and entry.device_id in area_device_ids
            )
            if not in_area:
                continue

            eid = entry.entity_id
            domain = entry.domain
            state_obj = self.hass.states.get(eid)
            dev_class = (
                entry.device_class
                or entry.original_device_class
                or (state_obj.attributes.get("device_class") if state_obj else None)
            )
            unit = (
                entry.unit_of_measurement
                or (
                    state_obj.attributes.get("unit_of_measurement")
                    if state_obj
                    else None
                )
            )
            fname = (
                entry.name
                or entry.original_name
                or (state_obj.attributes.get("friendly_name", "") if state_obj else "")
                or ""
            ).lower()
            eid_low = eid.lower()

            if domain == "sensor" and (
                dev_class == "illuminance" or unit in ("lx", "lm/m²")
            ):
                lux_list.append(eid)
                is_helper = (
                    entry.device_id is None
                    or eid_low.startswith("sensor.illuminance_")
                    or eid_low.startswith("sensor.average_")
                    or eid_low.startswith("sensor.mean_")
                    or entry.platform in ("group", "template", "min_max", "filter")
                )
                if is_helper:
                    helper_lux_list.append(eid)
                else:
                    physical_lux_list.append(eid)

            elif domain == "sensor" and (
                dev_class == "temperature" or unit in ("°C", "°F")
            ):
                if (
                    entry.device_id in climate_or_trv_device_ids
                    or any(kw in eid_low or kw in fname for kw in trv_keywords)
                    or "device_temperature" in eid_low
                ):
                    continue
                temp_list.append(eid)

            elif domain == "sensor" and dev_class == "humidity":
                if (
                    entry.device_id in climate_or_trv_device_ids
                    or any(kw in eid_low or kw in fname for kw in trv_keywords)
                ):
                    continue
                hum_list.append(eid)

            elif domain == "binary_sensor" and dev_class in (
                "motion",
                "occupancy",
                "presence",
            ):
                # Exclude Aqara PIR 10-min synthetic occupancy cluster & contact/opening entities
                if (
                    ("lumi" in eid_low and "sensor_motion" in eid_low and "occupancy" in eid_low)
                    or eid_low.endswith("_opening")
                ):
                    continue
                pres_list.append(eid)
                is_pres_helper = (
                    entry.device_id is None
                    or eid_low.startswith("binary_sensor.motion_")
                    or eid_low.startswith("binary_sensor.presence_")
                    or "group_occupancy" in eid_low
                    or entry.platform in ("group", "template", "bayesian")
                )
                if is_pres_helper:
                    helper_pres_list.append(eid)
                else:
                    physical_pres_list.append(eid)

            elif domain == "media_player":
                media_list.append(eid)

            elif domain == "light":
                if (
                    "segment" in eid_low
                    or "segment" in fname
                    or eid_low.endswith("_status_led")
                    or eid_low.endswith("_mmwave_led")
                    or eid_low.endswith("_indicator_led")
                ):
                    continue
                light_list.append(eid)

        # Deduplicate Light Group wrapper entities if their member lights are in the Area
        light_set = set(light_list)
        deduped_lights: list[str] = []
        for leid in light_list:
            lst = self.hass.states.get(leid)
            members = lst.attributes.get("entity_id") if lst else None
            if (
                isinstance(members, (list, tuple))
                and len(members) > 0
                and any(m in light_set and m != leid for m in members)
            ):
                continue
            deduped_lights.append(leid)
        if deduped_lights:
            light_list = deduped_lights

        self.all_area_entities = {
            "illuminance_sensors": sorted(lux_list),
            "temperature_sensors": sorted(temp_list),
            "humidity_sensors": sorted(hum_list),
            "presence_sensors": sorted(pres_list),
            "media_players": sorted(media_list),
            "lights": sorted(light_list),
        }

        # Automatically exclude aggregate helpers when physical sensors exist so we never double-count!
        auto_helpers: set[str] = set()
        if physical_lux_list and helper_lux_list:
            auto_helpers.update(helper_lux_list)
        if physical_pres_list and helper_pres_list:
            auto_helpers.update(helper_pres_list)
        self.auto_excluded_helpers = sorted(auto_helpers)

        excluded = self.excluded_entities | auto_helpers
        self.lux_sensors = [e for e in self.all_area_entities["illuminance_sensors"] if e not in excluded]
        if not self.lux_sensors and self.all_area_entities["illuminance_sensors"]:
            self.lux_sensors = [
                e for e in self.all_area_entities["illuminance_sensors"]
                if e not in self.excluded_entities
            ]

        self.temp_sensors = [e for e in self.all_area_entities["temperature_sensors"] if e not in self.excluded_entities]
        self.humidity_sensors = [e for e in self.all_area_entities["humidity_sensors"] if e not in self.excluded_entities]
        self.presence_sensors = [e for e in self.all_area_entities["presence_sensors"] if e not in excluded]
        if not self.presence_sensors and self.all_area_entities["presence_sensors"]:
            self.presence_sensors = [
                e for e in self.all_area_entities["presence_sensors"]
                if e not in self.excluded_entities
            ]

        self.media_players = (
            [e for e in self.all_area_entities["media_players"] if e not in self.excluded_entities]
            if self.config.get(CONF_INCLUDE_MEDIA_PLAYERS, True)
            else []
        )
        self.lights = [e for e in self.all_area_entities["lights"] if e not in self.excluded_entities]

        # Build rich light metadata & maintain `light_priority_order` (1st to shed -> last light standing)
        new_meta: dict[str, dict[str, Any]] = {}
        for leid in self.lights:
            ent_entry = ent_reg.async_get(leid)
            dev_entry = dev_reg.async_get(ent_entry.device_id) if (ent_entry and ent_entry.device_id) else None
            st_obj = self.hass.states.get(leid)
            fname = (
                (st_obj.attributes.get("friendly_name") if st_obj else None)
                or (ent_entry.name if ent_entry else None)
                or (ent_entry.original_name if ent_entry else None)
                or leid
            )
            model_str = f"{dev_entry.manufacturer or ''} {dev_entry.model or ''}".strip() if dev_entry else ""
            c_modes = (st_obj.attributes.get("supported_color_modes") if st_obj else None) or []
            meta = _classify_light_entity(leid, str(fname), model_str, c_modes)
            if st_obj:
                meta["state"] = st_obj.state
                bri_255 = st_obj.attributes.get("brightness")
                meta["brightness_pct"] = (
                    int(round(float(bri_255) * 100.0 / 255.0))
                    if (st_obj.state == STATE_ON and bri_255 is not None)
                    else (100 if st_obj.state == STATE_ON else 0)
                )
            else:
                meta["state"] = STATE_OFF
                meta["brightness_pct"] = 0
            new_meta[leid] = meta

        self.light_metadata = new_meta

        # Sync `light_priority_order` with `self.lights`
        existing_valid = [e for e in self.light_priority_order if e in self.lights]
        missing_lights = [e for e in self.lights if e not in existing_valid]
        missing_sorted = sorted(
            missing_lights,
            key=lambda e: (
                new_meta[e]["heuristic_rank"],
                new_meta[e]["friendly_name"].lower(),
            ),
        )
        if self.light_order_locked and existing_valid:
            self.light_priority_order = existing_valid + missing_sorted
        elif not existing_valid:
            self.light_priority_order = sorted(
                self.lights,
                key=lambda e: (
                    new_meta[e]["heuristic_rank"],
                    new_meta[e]["friendly_name"].lower(),
                ),
            )
        else:
            self.light_priority_order = existing_valid + missing_sorted

        # Sync linked HA scenes from config or area
        linked_scenes = list(self.config.get(CONF_LINKED_HA_SCENES, []))
        for entry in ent_reg.entities.values():
            if entry.domain == "scene" and not entry.disabled_by:
                in_area = entry.area_id == self.area_id or (
                    entry.area_id is None and entry.device_id in area_device_ids
                )
                if in_area and entry.entity_id not in linked_scenes and entry.entity_id not in self.excluded_entities:
                    linked_scenes.append(entry.entity_id)

        for scene_eid in linked_scenes:
            sid = f"ha_{scene_eid.replace('scene.', '')}"
            if sid not in self.adaptive_scenes:
                st = self.hass.states.get(scene_eid)
                friendly = (
                    st.attributes.get("friendly_name", scene_eid) if st else scene_eid
                )
                clean_name = friendly
                if clean_name.lower().startswith(self.room_name.lower()):
                    clean_name = clean_name[len(self.room_name) :].strip(" -_:") or friendly
                self.adaptive_scenes[sid] = AdaptiveSceneSpec(
                    scene_id=sid,
                    name=clean_name,
                    activity=self._guess_activity_from_name(clean_name),
                    reference_deficit_pct=65.0,
                    linked_ha_scene_entity=scene_eid,
                )

    @staticmethod
    def _guess_activity_from_name(name: str) -> str:
        """Guess a perceptual CCT profile from a scene name."""
        low = name.lower()
        if any(w in low for w in ("work", "meeting", "study", "office", "desk", "daylight", "bright")):
            return "working"
        if any(w in low for w in ("movie", "film", "cinema", "projector", "tv")):
            return "film"
        if any(w in low for w in ("read", "book")):
            return "reading"
        if any(w in low for w in ("red", "night", "bed", "sleep", "low")):
            return "sleep_prep"
        return ACTIVITY_RELAXING

    def get_available_scene_options(self) -> dict[str, str]:
        """Return mapping of scene_id -> human label for UI, Regime selectors, and Select entity."""
        options: dict[str, str] = {
            "default": "Auto (Regime / Trigger Default)",
            "all_lights": "All Room Lights (Dynamic)",
        }
        for sid, spec in self.adaptive_scenes.items():
            options[sid] = f"Scene: {spec.name}"
        return options

    def _resolve_active_scene(self, regime: str) -> tuple[str, bool, str]:
        """Determine (scene_id, scale_dynamically, reason_str) for the current regime and entity triggers."""
        # 1. Manual scene selection while occupied takes top priority
        if self.manual_scene_override_id and self.manual_scene_override_id not in ("default", ""):
            opts = self.get_available_scene_options()
            label = opts.get(self.manual_scene_override_id, self.manual_scene_override_id)
            return self.manual_scene_override_id, True, f"Manual Selection ({label})"

        # 2. Check Per-Time-of-Day Scene Triggers (`scene_triggers`)
        #    Check regime-specific triggers first, then "any" regime triggers;
        #    within each, check specific entity triggers before presence_only rules.
        ordered_triggers = [
            t for t in self.scene_triggers if t.get("regime") == regime
        ] + [
            t for t in self.scene_triggers if t.get("regime") in ("any", None, "")
        ]
        entity_triggers = [
            t for t in ordered_triggers if t.get("entity_id") not in (None, "", "presence_only")
        ]
        presence_triggers = [
            t for t in ordered_triggers if t.get("entity_id") in (None, "", "presence_only")
        ]

        for trig in entity_triggers:
            teid = trig.get("entity_id")
            target_states = [str(s).lower() for s in (trig.get("states") or ["on", "playing"])]
            target_scene = str(trig.get("scene_id") or "all_lights")
            scale_dyn = bool(trig.get("scale_dynamically", trig.get("dynamic_scaling", True)))
            st = self.hass.states.get(teid)
            if st and st.state.lower() in target_states:
                fname = st.attributes.get("friendly_name", teid)
                return (
                    target_scene,
                    scale_dyn,
                    f"Trigger ({fname} = {st.state})",
                )

        if self.is_occupied and presence_triggers:
            trig = presence_triggers[0]
            target_scene = str(trig.get("scene_id") or "all_lights")
            scale_dyn = bool(trig.get("scale_dynamically", trig.get("dynamic_scaling", True)))
            opts = self.get_available_scene_options()
            s_label = opts.get(target_scene, target_scene)
            return (
                target_scene,
                scale_dyn,
                f"Presence Rule — {regime.title()} ({s_label})",
            )

        # 3. Otherwise use the active Time-of-Day Regime's Default Occupied Scene
        rcfg = self.regime_configs.get(regime, DEFAULT_REGIME_SETTINGS.get(regime, {}))
        occ_scene = str(rcfg.get("occupied_scene") or "all_lights")
        opts = self.get_available_scene_options()
        s_label = opts.get(occ_scene, occ_scene)
        return occ_scene, True, f"{regime.title()} Default ({s_label})"

    @callback
    def async_evaluate_room(
        self, outdoor: OutdoorEnvironmentState, trigger_control: bool = False
    ) -> None:
        """Recalculate room Perceptive Illuminance and optionally run controller logic."""
        # 1. Read & smooth indoor lux sensors
        current_readings: dict[str, float] = {}
        for eid in self.lux_sensors:
            st = self.hass.states.get(eid)
            if st and st.state not in (STATE_UNKNOWN, STATE_UNAVAILABLE):
                try:
                    raw_val = float(st.state)
                    prev = self.smoothed_indoor_lux.get(eid, raw_val)
                    smoothed = 0.45 * raw_val + 0.55 * prev
                    self.smoothed_indoor_lux[eid] = smoothed
                    current_readings[eid] = smoothed
                except (ValueError, TypeError):
                    pass

        # 2. Inspect active room lights & compute real measured light contribution
        active_brightness_ratios: list[float] = []
        any_light_on = False
        for light_eid in self.lights:
            lst = self.hass.states.get(light_eid)
            if lst and lst.state == STATE_ON:
                any_light_on = True
                bri = lst.attributes.get("brightness")
                ratio = (float(bri) / 255.0) if bri is not None else 1.0
                active_brightness_ratios.append(ratio)
            if light_eid in self.light_metadata and lst:
                self.light_metadata[light_eid]["state"] = lst.state
                bri_255 = lst.attributes.get("brightness")
                self.light_metadata[light_eid]["brightness_pct"] = (
                    int(round(float(bri_255) * 100.0 / 255.0))
                    if (lst.state == STATE_ON and bri_255 is not None)
                    else (100 if lst.state == STATE_ON else 0)
                )

        self.controlled_lights_are_on = any_light_on
        if not any_light_on and current_readings:
            self.pre_light_natural_lux = sum(current_readings.values()) / len(
                current_readings
            )

        if any_light_on and active_brightness_ratios:
            avg_bri_ratio = sum(active_brightness_ratios) / max(1, len(self.lights))
            # Scale the room's actual measured max light delta by current brightness ratio
            artificial_lux_est = self.measured_max_light_delta_lux * avg_bri_ratio
            if self.pre_light_natural_lux is not None and current_readings:
                raw_avg = sum(current_readings.values()) / len(current_readings)
                measured_jump = max(0.0, raw_avg - self.pre_light_natural_lux)
                if measured_jump > 0.2:
                    artificial_lux_est = measured_jump
                    if avg_bri_ratio >= 0.35:
                        est_max = clamp(measured_jump / avg_bri_ratio, 1.0, 45.0)
                        self.measured_max_light_delta_lux = (
                            0.75 * self.measured_max_light_delta_lux + 0.25 * est_max
                        )
        else:
            artificial_lux_est = 0.0

        # 3. Read standalone room environment sensors (Temperature & Humidity, TRVs excluded)
        temp_vals: list[float] = []
        for teid in self.temp_sensors:
            tst = self.hass.states.get(teid)
            if tst and tst.state not in (STATE_UNKNOWN, STATE_UNAVAILABLE):
                try:
                    tval = float(tst.state)
                    if tst.attributes.get("unit_of_measurement") == "°F":
                        tval = (tval - 32.0) * 5.0 / 9.0
                    temp_vals.append(tval)
                except (ValueError, TypeError):
                    pass
        indoor_temp_c = (sum(temp_vals) / len(temp_vals)) if temp_vals else None

        hum_vals: list[float] = []
        for heid in self.humidity_sensors:
            hst = self.hass.states.get(heid)
            if hst and hst.state not in (STATE_UNKNOWN, STATE_UNAVAILABLE):
                try:
                    hum_vals.append(float(hst.state))
                except (ValueError, TypeError):
                    pass
        indoor_humidity_pct = (sum(hum_vals) / len(hum_vals)) if hum_vals else None

        # 4. Resolve active Scene & Trigger for current Time-of-Day Regime
        resolved_scene, _, scene_reason = self._resolve_active_scene(outdoor.time_regime)
        self.resolved_scene_id = resolved_scene
        self.resolved_scene_reason = scene_reason
        if resolved_scene in self.adaptive_scenes:
            self.active_activity = self.adaptive_scenes[resolved_scene].activity
        elif resolved_scene.startswith("activity_"):
            act_k = resolved_scene.replace("activity_", "", 1)
            if act_k in ACTIVITY_DEFINITIONS:
                self.active_activity = act_k

        # 5. Run Closed-Loop Perceptive Illuminance Engine
        sun_dir = self.config.get(CONF_SUN_WINDOW_DIRECTION, SUN_DIR_AUTO)
        if self.window_azimuth is not None and sun_dir == SUN_DIR_AUTO:
            sun_dir = SUN_DIR_COMPASS

        rspec = self.learned_weights.get_regime_spec(outdoor.time_regime)

        self.result = compute_room_perceptive_illuminance(
            indoor_sensor_readings=current_readings,
            sensor_profiles=self.sensor_profiles,
            outdoor=outdoor,
            activity_key=self.active_activity,
            daylight_level=self.config.get(CONF_DAYLIGHT_LEVEL, DAYLIGHT_MEDIUM),
            sun_window_direction=sun_dir,
            window_azimuth=self.window_azimuth,
            artificial_lux_estimate=artificial_lux_est,
            min_artificial_lux=max(1.0, self.measured_max_light_delta_lux * 0.15),
            lights_are_on=any_light_on,
            illum_model=rspec.illum_model,
            weather_model=rspec.weather_model,
            season_model=rspec.season_model,
            regime_target_scale=rspec.target_scale,
            indoor_temp_c=indoor_temp_c,
            indoor_humidity_pct=indoor_humidity_pct,
            regime_configs=self.regime_configs,
        )

        # 6. Evaluate occupancy (presence sensors + optional media players)
        was_occupied = self.is_occupied
        self.is_occupied = self._compute_occupancy()

        # 7. Clear Manual Override & Manual Scene Selection whenever room becomes unoccupied
        if not self.is_occupied:
            if self.manual_override_active:
                self.manual_override_active = False
                self.manual_override_baseline_natural_pct = None
            if self.manual_scene_override_id is not None:
                self.manual_scene_override_id = None
                self.active_scene_id = "default"
        elif (
            self.manual_override_active
            and self.manual_override_baseline_natural_pct is not None
        ):
            shift = abs(
                self.result.natural_perceptive_illuminance_pct
                - self.manual_override_baseline_natural_pct
            )
            if shift >= DEFAULT_SUBSTANTIAL_LUX_SHIFT_PCT:
                self.manual_override_active = False
                self.manual_override_baseline_natural_pct = None

        self._update_decision_explanation()

        async_dispatcher_send(
            self.hass, f"{SIGNAL_ROOM_UPDATED}_{self.room_id}"
        )

        # 8. Execute controller actions if in controller mode
        if trigger_control and self.operating_mode in (
            MODE_SIMPLE_TRANSIT,
            MODE_FULL_ADAPTIVE,
        ):
            self.hass.async_create_task(
                self._async_run_lighting_controller(
                    occupancy_just_changed=(self.is_occupied != was_occupied)
                )
            )

    def _compute_occupancy(self) -> bool:
        """Return True if any presence sensor or active media player indicates occupancy, recording which ones."""
        active_by: list[str] = []
        for peid in self.presence_sensors:
            st = self.hass.states.get(peid)
            if st and st.state == STATE_ON:
                active_by.append(st.attributes.get("friendly_name", peid))

        for meid in self.media_players:
            st = self.hass.states.get(meid)
            if st and st.state in (STATE_PLAYING, "paused", "buffering"):
                active_by.append(f"{st.attributes.get('friendly_name', meid)} ({st.state})")

        self.occupied_by_entities = active_by
        return len(active_by) > 0

    def _update_decision_explanation(self) -> None:
        """Build a plain-English diagnostic explanation of what the room controller is doing and why."""
        res = self.result
        reg = res.time_regime
        rcfg = self.regime_configs.get(reg, DEFAULT_REGIME_SETTINGS.get(reg, {}))
        off_scene = str(rcfg.get("off_scene", "off"))
        opts = self.get_available_scene_options()

        if self.operating_mode == MODE_SENSOR_ONLY:
            self.decision_reason = (
                f"Sensor-Only Mode • Total Room Lux {res.effective_spatial_lux:.1f} lx "
                f"(Turn-On Threshold {res.turn_on_lux_threshold:.1f} lx)"
            )
            return

        if self.automation_locked or self.hub.global_lock:
            self.decision_reason = "Automation Locked • Manual control only"
            return

        if not self.is_occupied:
            if off_scene != "off" and rcfg.get("off_scene_when_unoccupied", False):
                self.decision_reason = (
                    f"Vacant • Applying {reg.title()} Off-State Override ({opts.get(off_scene, off_scene)})"
                )
            else:
                self.decision_reason = "Vacant • All controlled room lights OFF"
            return

        occ_str = ", ".join(self.occupied_by_entities[:2]) if self.occupied_by_entities else "Occupied"
        if self.manual_override_active:
            self.decision_reason = (
                f"Occupied ({occ_str}) • Manual Override Active at {self._get_current_on_brightness_pct()}% "
                f"(Total Lux {res.effective_spatial_lux:.1f} lx) — Resets on vacancy"
            )
            return

        if res.should_turn_lights_on and res.recommended_brightness_pct > 0:
            shed_note = (
                f" • Shed {self.active_shed_count}/{len(self.lights)} brightest lights"
                if self.active_shed_count > 0
                else ""
            )
            self.decision_reason = (
                f"Occupied ({occ_str}) • Total Lux {res.effective_spatial_lux:.1f} lx "
                f"(Turn-On < {res.turn_on_lux_threshold:.1f} lx, Target {res.target_comfort_lux:.1f} lx) "
                f"→ {self.resolved_scene_reason} at {res.recommended_brightness_pct}%{shed_note}"
            )
        else:
            if off_scene != "off" and rcfg.get("off_scene_when_bright", False):
                self.decision_reason = (
                    f"Occupied ({occ_str}) • Daylight Sufficient ({res.effective_spatial_lux:.1f} lx >= "
                    f"{res.turn_on_lux_threshold:.1f} lx) → Using Off-State Scene ({opts.get(off_scene, off_scene)})"
                )
            else:
                self.decision_reason = (
                    f"Occupied ({occ_str}) • Daylight Sufficient ({res.effective_spatial_lux:.1f} lx >= "
                    f"{res.turn_on_lux_threshold:.1f} lx) → Lights OFF"
                )

    def _is_blocked_by_inhibit_entity(self) -> bool:
        """Return True if any configured blocker entity is currently ON."""
        for beid in self.config.get(CONF_BLOCKER_ENTITIES, []):
            st = self.hass.states.get(beid)
            if st and st.state == STATE_ON:
                return True
        return False

    def _get_current_on_brightness_pct(self) -> int:
        """Return current average brightness percentage (1-100) of ON dimmable lights in the room, or 0 if all OFF."""
        dim_vals: list[float] = []
        any_on = False
        for leid in self.lights:
            st = self.hass.states.get(leid)
            if st and st.state == STATE_ON:
                any_on = True
                bri = st.attributes.get("brightness")
                if bri is not None:
                    dim_vals.append(max(1.0, float(bri) * 100.0 / 255.0))
        if dim_vals:
            return int(clamp(round(sum(dim_vals) / len(dim_vals)), 1, 100))
        return 100 if any_on else 0

    async def _async_run_lighting_controller(
        self, occupancy_just_changed: bool = False, force_apply: bool = False
    ) -> None:
        """Execute presence, scene, progressive light shedding, and off-state scene override control."""
        if self.operating_mode == MODE_SENSOR_ONLY:
            return
        if (
            self.automation_locked
            or self.hub.global_lock
            or self._is_blocked_by_inhibit_entity()
        ):
            return

        now = datetime.now(timezone.utc)
        reg = self.result.time_regime
        rcfg = self.regime_configs.get(reg, DEFAULT_REGIME_SETTINGS.get(reg, {}))
        off_scene = str(rcfg.get("off_scene", "off"))

        # Case 1: Room is UNOCCUPIED -> Immediately turn lights OFF or apply the Regime's Off-State Scene Override!
        if not self.is_occupied and not force_apply:
            self.manual_override_active = False
            self.manual_override_baseline_natural_pct = None
            self.active_shed_count = 0
            self.shed_light_entities = []

            if off_scene != "off" and rcfg.get("off_scene_when_unoccupied", False):
                if not self.off_scene_currently_active or occupancy_just_changed:
                    await self._async_apply_scene_by_id(
                        off_scene,
                        target_brightness_pct=int(rcfg.get("target_brightness_pct", 35)),
                        target_cct_kelvin=self.result.recommended_cct_kelvin,
                        scale_dynamically=False,
                        shed_count=0,
                        transition=3,
                    )
                    self.off_scene_currently_active = True
            else:
                self.off_scene_currently_active = False
                if self.controlled_lights_are_on:
                    await self._async_turn_off_room_lights(transition=3)
                    self.controlled_lights_are_on = False
            return

        if self.manual_override_active and not force_apply:
            return

        # Case 2: Room is OCCUPIED (or force_apply from calibration / scene selector)
        rec_bri = self.result.recommended_brightness_pct
        rec_cct = self.result.recommended_cct_kelvin
        rec_shed = self.result.recommended_shed_count
        scene_id, scale_dyn, _ = self._resolve_active_scene(reg)

        wants_lights_on = self.result.should_turn_lights_on and rec_bri > 0
        wants_lights_off = self.result.should_turn_lights_off_while_occupied

        if not self.controlled_lights_are_on or self.off_scene_currently_active:
            if wants_lights_on or (force_apply and not wants_lights_off):
                if (
                    not occupancy_just_changed
                    and not force_apply
                    and self.cooldown_active
                ):
                    return
                self.off_scene_currently_active = False
                self.active_shed_count = rec_shed
                await self._async_apply_scene_by_id(
                    scene_id,
                    target_brightness_pct=max(1, rec_bri),
                    target_cct_kelvin=rec_cct,
                    scale_dynamically=scale_dyn,
                    shed_count=rec_shed,
                    transition=2 if (occupancy_just_changed or force_apply) else 12,
                )
                self.last_on_off_change_utc = now
                self.last_adjustment_utc = now
        else:
            # Lights are currently ON while room is occupied
            if wants_lights_off:
                if self.cooldown_active and not force_apply:
                    return
                self.active_shed_count = 0
                self.shed_light_entities = []
                if off_scene != "off" and rcfg.get("off_scene_when_bright", False):
                    await self._async_apply_scene_by_id(
                        off_scene,
                        target_brightness_pct=int(rcfg.get("target_brightness_pct", 35)),
                        target_cct_kelvin=rec_cct,
                        scale_dynamically=False,
                        shed_count=0,
                        transition=3 if force_apply else 12,
                    )
                    self.off_scene_currently_active = True
                else:
                    await self._async_turn_off_room_lights(transition=3 if force_apply else 12)
                    self.controlled_lights_are_on = False
                    self.off_scene_currently_active = False
                self.last_on_off_change_utc = now
                return

            # Lights stay ON -> Check 5-minute gradual adjustment interval (bypassed during active calibration/force_apply)
            adjust_mins = float(
                self.config.get(
                    CONF_ADJUST_INTERVAL_MINUTES, DEFAULT_ADJUST_INTERVAL_MINUTES
                )
            )
            if not force_apply and self.last_adjustment_utc is not None:
                elapsed_adj = (now - self.last_adjustment_utc).total_seconds()
                if elapsed_adj < (adjust_mins * 60.0):
                    return

            transition_sec = int(
                self.config.get(
                    CONF_TRANSITION_SECONDS, DEFAULT_TRANSITION_SECONDS
                )
            )
            self.active_shed_count = rec_shed
            await self._async_apply_scene_by_id(
                scene_id,
                target_brightness_pct=max(1, rec_bri),
                target_cct_kelvin=rec_cct,
                scale_dynamically=scale_dyn,
                shed_count=rec_shed,
                transition=2 if force_apply else transition_sec,
            )
            self.last_adjustment_utc = now

    def _compute_shed_split(
        self, candidate_lights: list[str], target_brightness_pct: int, shed_count: int
    ) -> tuple[list[str], list[str]]:
        """Split `candidate_lights` into `(lights_to_keep_on, lights_to_shed_off)` using `self.light_priority_order`.

        - Non-dimmable (`onoff_switch`) lights automatically shed when `target_brightness_pct <= 30%`.
        - Up to `shed_count` lights from the front of `light_priority_order` are turned OFF, always
          leaving at least 1 light (the final softest light in `light_priority_order`) ON!
        """
        if not candidate_lights:
            return [], []

        ordered = [e for e in self.light_priority_order if e in candidate_lights]
        for e in candidate_lights:
            if e not in ordered:
                ordered.append(e)

        if len(ordered) <= 1:
            self.shed_light_entities = []
            return ordered, []

        max_shed = len(ordered) - 1
        eff_shed = int(clamp(shed_count, 0, max_shed))

        to_shed: list[str] = []
        # 1. If dimming <= 30%, shed non-dimmable on/off switches first
        if target_brightness_pct <= 30:
            for e in ordered:
                if len(to_shed) >= max_shed:
                    break
                meta = self.light_metadata.get(e, {})
                if not meta.get("is_dimmable", True):
                    to_shed.append(e)

        # 2. Shed additional lights in priority order up to `eff_shed`
        for e in ordered:
            if len(to_shed) >= eff_shed or len(to_shed) >= max_shed:
                break
            if e not in to_shed:
                to_shed.append(e)

        to_keep = [e for e in ordered if e not in to_shed]
        self.shed_light_entities = list(to_shed)
        return to_keep, to_shed

    async def _async_apply_scene_by_id(
        self,
        scene_id: str,
        target_brightness_pct: int,
        target_cct_kelvin: int,
        scale_dynamically: bool = True,
        shed_count: int = 0,
        transition: int = 12,
    ) -> None:
        """Apply either a specific Adaptive/HA Scene or All Room Lights, honoring `1%` minimum and `shed_count`."""
        cct_mode = self.config.get(CONF_CCT_MODE, CCT_EXTERNAL)
        apply_cct = cct_mode == CCT_KRUITHOF
        clamped_bri = int(clamp(target_brightness_pct, 1, 100))

        spec = self.adaptive_scenes.get(scene_id)
        if spec:
            await self._async_apply_calibrated_scene(
                spec,
                target_brightness_pct=clamped_bri,
                target_cct_kelvin=target_cct_kelvin if apply_cct else None,
                scale_dynamically=scale_dynamically,
                shed_count=shed_count,
                transition=transition,
            )
            return

        # Apply to All Room Lights with progressive light shedding
        to_keep, to_shed = self._compute_shed_split(
            self.lights, clamped_bri, shed_count
        )

        if to_shed:
            on_shed = [
                e for e in to_shed
                if (st := self.hass.states.get(e)) and st.state == STATE_ON
            ]
            if on_shed:
                await self._async_call_light_service(
                    STATE_OFF, on_shed, {"transition": min(transition, 3)}
                )

        if to_keep:
            dimmable_keep = [
                e for e in to_keep
                if self.light_metadata.get(e, {}).get("is_dimmable", True)
            ]
            onoff_keep = [e for e in to_keep if e not in dimmable_keep]

            if dimmable_keep:
                sdata: dict[str, Any] = {
                    "brightness_pct": clamped_bri,
                    "transition": transition,
                }
                if apply_cct:
                    sdata["color_temp_kelvin"] = target_cct_kelvin
                await self._async_call_light_service(STATE_ON, dimmable_keep, sdata)

            if onoff_keep:
                await self._async_call_light_service(STATE_ON, onoff_keep, {})

        self.controlled_lights_are_on = bool(to_keep)

    async def _async_apply_calibrated_scene(
        self,
        spec: AdaptiveSceneSpec,
        target_brightness_pct: int,
        target_cct_kelvin: int | None,
        scale_dynamically: bool = True,
        shed_count: int = 0,
        transition: int = 12,
    ) -> None:
        """Apply an Adaptive/HA Scene scaled dynamically to `target_brightness_pct` (1%..100%) and `shed_count`."""
        # If linked to a native HA scene without captured per-light snapshot yet, activate & snapshot it first
        if spec.linked_ha_scene_entity and not spec.light_states:
            ctx = self._make_context()
            self._ignore_light_events_until_utc = datetime.now(timezone.utc) + timedelta(
                seconds=max(6.0, float(min(transition, 5)) + 4.0)
            )
            await self.hass.services.async_call(
                "scene",
                "turn_on",
                {
                    ATTR_ENTITY_ID: spec.linked_ha_scene_entity,
                    "transition": min(transition, 4),
                },
                context=ctx,
            )
            await asyncio.sleep(0.8)
            self._snapshot_current_lights_into_scene(spec)

        if not spec.light_states:
            return

        scene_on_lights = [
            eid
            for eid, lstate in spec.light_states.items()
            if eid not in self.excluded_entities and lstate.get("state") == STATE_ON
        ]
        scene_off_lights = [
            eid
            for eid, lstate in spec.light_states.items()
            if eid not in self.excluded_entities and lstate.get("state") == STATE_OFF
        ]

        to_keep, to_shed = self._compute_shed_split(
            scene_on_lights, target_brightness_pct, shed_count
        )
        all_to_turn_off = list(set(scene_off_lights + to_shed))

        # Compute mean scene brightness so we scale relative to the scene's balance while hitting `target_brightness_pct`
        if scene_on_lights:
            mean_scene_bri = sum(
                float(spec.light_states[e].get("brightness_pct", 70.0))
                for e in scene_on_lights
            ) / len(scene_on_lights)
        else:
            mean_scene_bri = 70.0

        scale_ratio = (
            (float(target_brightness_pct) / max(10.0, mean_scene_bri))
            if scale_dynamically
            else 1.0
        )

        for eid in to_keep:
            lstate = spec.light_states.get(eid, {})
            is_dim = self.light_metadata.get(eid, {}).get("is_dimmable", True)
            if not is_dim:
                await self._async_call_light_service(STATE_ON, [eid], {})
                continue

            base_bri = float(lstate.get("brightness_pct", target_brightness_pct))
            if target_brightness_pct <= 1:
                adapted_bri = 1
            else:
                adapted_bri = int(clamp(round(base_bri * scale_ratio), 1, 100))

            sdata: dict[str, Any] = {
                "brightness_pct": adapted_bri,
                "transition": transition,
            }
            if target_cct_kelvin is not None:
                sdata["color_temp_kelvin"] = target_cct_kelvin
            elif lstate.get("color_temp_kelvin"):
                sdata["color_temp_kelvin"] = lstate["color_temp_kelvin"]
            elif lstate.get("rgb_color"):
                sdata["rgb_color"] = lstate["rgb_color"]

            await self._async_call_light_service(STATE_ON, [eid], sdata)

        if all_to_turn_off:
            currently_on_off_list = [
                e for e in all_to_turn_off
                if (st := self.hass.states.get(e)) and st.state == STATE_ON
            ]
            if currently_on_off_list:
                await self._async_call_light_service(
                    STATE_OFF, currently_on_off_list, {"transition": min(transition, 3)}
                )

        self.controlled_lights_are_on = bool(to_keep)

    def _snapshot_current_lights_into_scene(self, spec: AdaptiveSceneSpec) -> None:
        """Record current on/off, brightness, and color of room lights into an AdaptiveSceneSpec."""
        snapshot: dict[str, dict[str, Any]] = {}
        for eid in self.lights:
            st = self.hass.states.get(eid)
            if not st:
                continue
            if st.state == STATE_ON:
                bri_255 = st.attributes.get("brightness")
                bri_pct = (
                    int(round(float(bri_255) * 100.0 / 255.0))
                    if bri_255 is not None
                    else 80
                )
                snapshot[eid] = {
                    "state": STATE_ON,
                    "brightness_pct": max(1, bri_pct),
                    "color_temp_kelvin": st.attributes.get("color_temp_kelvin"),
                    "rgb_color": st.attributes.get("rgb_color"),
                }
            else:
                snapshot[eid] = {"state": STATE_OFF, "brightness_pct": 0}
        spec.light_states = snapshot

    async def _async_turn_off_room_lights(self, transition: int = 5) -> None:
        """Turn off all non-excluded lights in the room."""
        on_lights = [
            e
            for e in self.lights
            if (st := self.hass.states.get(e)) and st.state == STATE_ON
        ]
        if on_lights:
            await self._async_call_light_service(
                STATE_OFF, on_lights, {"transition": transition}
            )

    async def _async_call_light_service(
        self, action: str, entity_ids: list[str], data: dict[str, Any]
    ) -> None:
        """Call light.turn_on or light.turn_off with tracked context ID & transition guard."""
        if not entity_ids:
            return
        ctx = self._make_context()
        trans_sec = float(data.get("transition", 2))
        self._ignore_light_events_until_utc = datetime.now(timezone.utc) + timedelta(
            seconds=max(6.0, trans_sec + 4.0)
        )
        service = "turn_on" if action == STATE_ON else "turn_off"
        payload = {ATTR_ENTITY_ID: entity_ids}
        payload.update(data)
        try:
            await self.hass.services.async_call(
                "light", service, payload, context=ctx
            )
        except Exception as err:  # noqa: BLE001
            _LOGGER.debug(
                "[%s] Light call %s fallback without transition: %s",
                self.room_name,
                service,
                err,
            )
            fallback_payload = {ATTR_ENTITY_ID: entity_ids}
            if service == "turn_on" and "brightness_pct" in data:
                fallback_payload["brightness_pct"] = data["brightness_pct"]
            await self.hass.services.async_call(
                "light", service, fallback_payload, context=ctx
            )

    def _make_context(self) -> Context:
        """Create and track a Home Assistant Context so our own light calls aren't flagged as manual overrides."""
        ctx = Context()
        self._recent_context_ids.add(ctx.id)
        if len(self._recent_context_ids) > 100:
            while len(self._recent_context_ids) > 60:
                self._recent_context_ids.pop()
        return ctx

    # =========================================================================
    # Debounced Manual Light Listener & 5-Second Settled Total Lux Measurement
    # =========================================================================

    @callback
    def async_handle_light_state_event(self, event: Event) -> None:
        """Detect manual light adjustments with a 4-second multi-bulb debounce window."""
        if self.operating_mode == MODE_SENSOR_ONLY or self.automation_locked:
            return
        if not self.is_occupied:
            return

        new_state = event.data.get("new_state")
        old_state = event.data.get("old_state")
        if new_state is None or old_state is None:
            return
        if new_state.state in (STATE_UNKNOWN, STATE_UNAVAILABLE):
            return
        if old_state.state in (STATE_UNKNOWN, STATE_UNAVAILABLE):
            return

        ctx = event.context
        if (
            ctx.id in self._recent_context_ids
            or ctx.parent_id in self._recent_context_ids
        ):
            return
        if (
            self._ignore_light_events_until_utc is not None
            and datetime.now(timezone.utc) < self._ignore_light_events_until_utc
        ):
            return

        old_bri = old_state.attributes.get("brightness")
        new_bri = new_state.attributes.get("brightness")
        state_changed = old_state.state != new_state.state
        bri_changed = (
            old_bri is not None
            and new_bri is not None
            and abs(int(old_bri) - int(new_bri)) > 10
        )

        if state_changed or bri_changed:
            self.manual_override_active = True
            self.manual_override_baseline_natural_pct = (
                self.result.natural_perceptive_illuminance_pct
            )
            if state_changed and new_state.state == STATE_OFF:
                self._pending_manual_event_type = "manual_light_off"
            elif state_changed and old_state.state == STATE_OFF:
                self._pending_manual_event_type = "manual_light_on"
            else:
                self._pending_manual_event_type = "manual_dim"

            # Debounce across all bulbs in the room over 4.5 seconds!
            if self._manual_debounce_unsub is not None:
                self._manual_debounce_unsub()
            self._manual_debounce_unsub = async_call_later(
                self.hass, 4.5, self._async_on_manual_change_settled
            )
            self._update_decision_explanation()
            async_dispatcher_send(
                self.hass, f"{SIGNAL_ROOM_UPDATED}_{self.room_id}"
            )

    @callback
    def _async_on_manual_change_settled(self, _now: datetime) -> None:
        """Called once 4.5 seconds after the user finishes adjusting a wall switch or dimmer slider."""
        self._manual_debounce_unsub = None
        self.async_evaluate_room(self.hub.outdoor_state, trigger_control=False)

        cur_bri = self._get_current_on_brightness_pct()
        lights_on = any(
            (st := self.hass.states.get(e)) and st.state == STATE_ON
            for e in self.lights
        )
        measured_total_lux = self.result.effective_spatial_lux
        r_weights = self.hub.outdoor_state.regime_weights or {
            self.hub.outdoor_state.time_regime: 1.0
        }

        if not lights_on:
            # User manually turned room lights OFF while occupied -> current lux is bright enough!
            for rkey, w in r_weights.items():
                if w >= 0.15:
                    cur_on = float(self.regime_configs[rkey]["turn_on_lux"])
                    desired_on = max(0.5, min(cur_on, measured_total_lux * 0.80))
                    self.regime_configs[rkey]["turn_on_lux"] = round(
                        (1.0 - w * 0.75) * cur_on + (w * 0.75) * desired_on, 1
                    )
        else:
            # User manually turned lights ON or set brightness -> adopt their brightness & measured Total Room Lux!
            for rkey, w in r_weights.items():
                if w >= 0.15:
                    cur_on = float(self.regime_configs[rkey]["turn_on_lux"])
                    if self._pending_manual_event_type == "manual_light_on":
                        pre_lux = self.pre_light_natural_lux or measured_total_lux
                        if pre_lux >= cur_on:
                            self.regime_configs[rkey]["turn_on_lux"] = round(
                                pre_lux + 2.0, 1
                            )
                    old_bri = float(self.regime_configs[rkey]["target_brightness_pct"])
                    self.regime_configs[rkey]["target_brightness_pct"] = int(
                        clamp(round((1.0 - w * 0.8) * old_bri + (w * 0.8) * cur_bri), 1, 100)
                    )
                    self.regime_configs[rkey]["target_total_lux"] = round(
                        max(1.5, measured_total_lux), 1
                    )

        self._record_observation_and_learn(
            event_type=self._pending_manual_event_type,
            desired_lights_on=lights_on,
            desired_bri_pct=cur_bri if lights_on else 0,
            shed_count=self.active_shed_count,
        )
        self.async_evaluate_room(self.hub.outdoor_state, trigger_control=False)
        self.hass.async_create_task(self.hub.async_save_runtime_state())

    def _schedule_settled_lux_measurement(
        self,
        event_type: str,
        desired_lights_on: bool,
        desired_bri_pct: int,
        shed_count: int,
    ) -> None:
        """Schedule a 5-second settled sensor read after a direct user button control (`+` / `-` / calibrate)."""
        if self._settle_measure_unsub is not None:
            self._settle_measure_unsub()

        @callback
        def _on_settled(_now: datetime) -> None:
            self._settle_measure_unsub = None
            # Re-read raw sensor states directly so we get the fresh post-transition Total Room Lux
            fresh_vals: list[float] = []
            for eid in self.lux_sensors:
                prof = self.sensor_profiles.get(eid)
                if prof and prof.suggest_blacklist:
                    continue
                st = self.hass.states.get(eid)
                if st and st.state not in (STATE_UNKNOWN, STATE_UNAVAILABLE):
                    try:
                        val = float(st.state)
                        self.smoothed_indoor_lux[eid] = val
                        fresh_vals.append(val)
                    except (ValueError, TypeError):
                        pass

            settled_lux = (
                sum(fresh_vals) / len(fresh_vals)
                if fresh_vals
                else self.result.effective_spatial_lux
            )
            r_weights = self.hub.outdoor_state.regime_weights or {
                self.hub.outdoor_state.time_regime: 1.0
            }

            if desired_lights_on:
                for rkey, w in r_weights.items():
                    if w >= 0.10:
                        self.regime_configs[rkey]["target_total_lux"] = round(
                            max(1.5, settled_lux), 1
                        )
            else:
                # Lights were turned OFF via "Feels Bright Enough": settled_lux is the unlit natural room lux!
                for rkey, w in r_weights.items():
                    if w >= 0.10:
                        cur_on = float(self.regime_configs[rkey]["turn_on_lux"])
                        new_on = max(0.5, min(cur_on * 0.85, settled_lux * 0.78))
                        self.regime_configs[rkey]["turn_on_lux"] = round(
                            (1.0 - w) * cur_on + w * new_on, 1
                        )

            self.async_evaluate_room(self.hub.outdoor_state, trigger_control=False)
            self._record_observation_and_learn(
                event_type=event_type,
                desired_lights_on=desired_lights_on,
                desired_bri_pct=desired_bri_pct,
                shed_count=shed_count,
            )
            self.hass.async_create_task(self.hub.async_save_runtime_state())

        self._settle_measure_unsub = async_call_later(self.hass, 5.0, _on_settled)

    @callback
    def _record_observation_and_learn(
        self,
        event_type: str,
        desired_lights_on: bool,
        desired_bri_pct: int,
        shed_count: int = 0,
    ) -> None:
        """Record a settled user observation, run full-history Factorial Model Fit, and conditionally trigger AI."""
        out = self.hub.outdoor_state
        obs = PerceptiveObservation(
            timestamp_utc=datetime.now(timezone.utc).isoformat(),
            event_type=event_type,
            time_regime=out.time_regime,
            activity=self.active_activity,
            indoor_raw_lux=self.result.effective_spatial_lux,
            natural_spatial_lux=self.result.natural_spatial_lux,
            outdoor_sky_lux=out.effective_sky_lux,
            weather_condition=out.weather_condition,
            cloud_cover_ratio=out.cloud_cover_ratio,
            diffuse_sky_factor=out.diffuse_sky_factor,
            solar_elevation=out.solar_elevation,
            solar_azimuth=out.solar_azimuth,
            is_daytime=out.is_daytime,
            outdoor_temp_c=out.outdoor_temp_c,
            indoor_temp_c=self.result.indoor_temp_c,
            indoor_humidity_pct=self.result.indoor_humidity_pct,
            day_length_hours=out.day_length_hours,
            direct_sun_in_window=self.result.direct_sun_in_window,
            internal_contrast_ratio=self.result.internal_contrast_ratio,
            model_target_lux=self.result.target_comfort_lux,
            model_deficit_pct=self.result.lighting_deficit_pct,
            model_recommended_bri_pct=self.result.recommended_brightness_pct,
            user_desired_lights_on=desired_lights_on,
            user_desired_bri_pct=desired_bri_pct,
            user_shed_count=shed_count,
        )
        self.observations.append(obs)
        if len(self.observations) > MAX_OBSERVATION_HISTORY:
            self.observations = self.observations[-MAX_OBSERVATION_HISTORY:]

        base_turn_on_map = {
            r: float(self.regime_configs[r]["turn_on_lux"]) for r in TIME_REGIMES
        }
        run_local_factorial_backtest(
            weights=self.learned_weights,
            observations=self.observations,
            daylight_level=self.config.get(CONF_DAYLIGHT_LEVEL, DAYLIGHT_MEDIUM),
            base_turn_on_map=base_turn_on_map,
            active_regime=out.time_regime,
        )

        self._obs_since_last_gemini_call += 1
        if (
            self._obs_since_last_gemini_call >= OBSERVATIONS_BEFORE_AUTO_AI_CALL
            and self.learned_weights.can_auto_call_ai_now()
        ):
            self._obs_since_last_gemini_call = 0
            self.hass.async_create_task(self.async_optimize_with_gemini())

    async def async_optimize_with_gemini(self) -> bool:
        """Invoke Home Assistant's Gemini AI (`ai_task` / `conversation`) to calibrate models, sensors & light priority."""
        ordered_meta = [
            self.light_metadata[eid]
            for eid in self.light_priority_order
            if eid in self.light_metadata
        ]
        parsed = await async_run_gemini_room_optimization(
            hass=self.hass,
            room_name=self.room_name,
            room_config=self.config,
            weights=self.learned_weights,
            observations=self.observations,
            sensor_profiles=self.sensor_profiles,
            current_outdoor=self.hub.outdoor_state,
            current_result=self.result,
            light_metadata=ordered_meta,
            regime_configs=self.regime_configs,
        )
        if isinstance(parsed, dict) and not self.light_order_locked:
            ai_order = parsed.get("light_priority_order")
            if isinstance(ai_order, list) and ai_order:
                valid_ai_order = [e for e in ai_order if e in self.lights]
                remaining = [e for e in self.lights if e not in valid_ai_order]
                if valid_ai_order:
                    self.light_priority_order = valid_ai_order + remaining

        self.async_evaluate_room(self.hub.outdoor_state, trigger_control=False)
        await self.hub.async_save_runtime_state()
        return parsed is not None

    # =========================================================================
    # User Actions, Direct Control (`1%` Floor + Light Shedding), & Config Updates
    # =========================================================================

    async def async_select_scene_or_activity(self, option_key: str) -> None:
        """Switch the room's active Scene and apply immediately."""
        self.manual_override_active = False
        self.manual_override_baseline_natural_pct = None

        if option_key == "default":
            self.active_scene_id = "default"
            self.manual_scene_override_id = None
        elif option_key == "all_lights":
            self.active_scene_id = "all_lights"
            self.manual_scene_override_id = "all_lights"
        elif option_key.startswith("activity_"):
            act = option_key.replace("activity_", "", 1)
            if act in ACTIVITY_DEFINITIONS:
                self.active_activity = act
                self.active_scene_id = f"activity_{act}"
                self.manual_scene_override_id = f"activity_{act}"
        elif option_key in self.adaptive_scenes:
            spec = self.adaptive_scenes[option_key]
            self.active_scene_id = option_key
            self.manual_scene_override_id = option_key
            self.active_activity = spec.activity
        else:
            for sid, spec in self.adaptive_scenes.items():
                if spec.name.lower() == option_key.lower() or f"scene: {spec.name.lower()}" == option_key.lower():
                    self.active_scene_id = sid
                    self.manual_scene_override_id = sid
                    self.active_activity = spec.activity
                    break

        self.async_evaluate_room(self.hub.outdoor_state, trigger_control=False)
        await self._async_run_lighting_controller(force_apply=True)
        await self.hub.async_save_runtime_state()

    async def async_capture_current_as_adaptive_scene(
        self, name: str, activity: str | None = None
    ) -> AdaptiveSceneSpec:
        """Snapshot the room's current light states as a reusable Adaptive Scene."""
        slug = "".join(c if c.isalnum() else "_" for c in name.lower()).strip("_")
        scene_id = f"captured_{slug}"
        act = activity if activity in ACTIVITY_DEFINITIONS else self.active_activity

        spec = AdaptiveSceneSpec(
            scene_id=scene_id,
            name=name,
            activity=act,
            reference_deficit_pct=max(20.0, self.result.lighting_deficit_pct),
        )
        self._snapshot_current_lights_into_scene(spec)
        self.adaptive_scenes[scene_id] = spec
        self.active_scene_id = scene_id
        self.manual_scene_override_id = scene_id
        self.manual_override_active = False

        await self.hub.async_save_runtime_state()
        async_dispatcher_send(
            self.hass, f"{SIGNAL_ROOM_UPDATED}_{self.room_id}"
        )
        return spec

    async def async_nudge_feel(
        self,
        direction: str,
        step_pct: float = DEFAULT_MICRO_NUDGE_STEP_PCT,
        record_ml: bool = True,
    ) -> None:
        """Directly step room brightness (`1%`..`100%`) or shed/unshed lights one by one at `1%`, then measure settled Total Room Lux!

        CRITICAL GUARANTEE: Pressing `-` ('too_bright') NEVER turns all lights off!
        """
        wants_more_light = direction in (
            "lighter",
            "more_light",
            "darker_room",
            "up",
            "+",
        )
        reg = self.hub.outdoor_state.time_regime
        r_weights = self.hub.outdoor_state.regime_weights or {reg: 1.0}
        rcfg = self.regime_configs.get(reg, DEFAULT_REGIME_SETTINGS[reg])

        current_on_bri = self._get_current_on_brightness_pct()
        lights_currently_on = any(
            (st := self.hass.states.get(e)) and st.state == STATE_ON
            for e in self.lights
        )
        max_shed = max(0, len(self.lights) - 1)

        if wants_more_light:
            # Stepping UP (+)
            if not lights_currently_on:
                # Lights were OFF: turn them ON and raise turn_on_lux above current room lux!
                new_bri = max(25, int(rcfg.get("target_brightness_pct", 65)))
                new_shed = 0
                cur_lux = self.result.effective_spatial_lux
                for rkey, w in r_weights.items():
                    if w >= 0.10:
                        old_on = float(self.regime_configs[rkey]["turn_on_lux"])
                        if cur_lux >= old_on:
                            self.regime_configs[rkey]["turn_on_lux"] = round(
                                cur_lux + 2.5, 1
                            )
            elif self.active_shed_count > 0 and current_on_bri <= 2:
                # Lights are at 1% with some lights shed -> turn one shed light back ON at 1%!
                new_bri = 1
                new_shed = max(0, self.active_shed_count - 1)
            else:
                new_shed = 0
                if current_on_bri < 5:
                    new_bri = 5
                elif current_on_bri < 10:
                    new_bri = 10
                elif current_on_bri < 20:
                    new_bri = 20
                else:
                    new_bri = int(clamp(round(current_on_bri + max(10.0, step_pct)), 1, 100))
        else:
            # Stepping DOWN (-) — NEVER turns all lights off!
            base_bri = current_on_bri if lights_currently_on else int(rcfg.get("target_brightness_pct", 65))
            if base_bri > 20:
                new_bri = max(12, int(round(base_bri - max(10.0, step_pct))))
                new_shed = self.active_shed_count
            elif base_bri > 10:
                new_bri = 10
                new_shed = self.active_shed_count
            elif base_bri > 5:
                new_bri = 5
                new_shed = self.active_shed_count
            elif base_bri > 1:
                new_bri = 1
                new_shed = self.active_shed_count
            else:
                # Already at 1% minimum floor! Shed one additional light source in priority order (keeping at least 1 light ON at 1%)
                new_bri = 1
                new_shed = min(max_shed, self.active_shed_count + 1)

        self.active_shed_count = new_shed

        # Proportionally update `target_brightness_pct` and `shed_count` across active blended regimes
        for rkey, w in r_weights.items():
            if w >= 0.10:
                old_b = float(self.regime_configs[rkey]["target_brightness_pct"])
                self.regime_configs[rkey]["target_brightness_pct"] = int(
                    clamp(round((1.0 - w) * old_b + w * new_bri), 1, 100)
                )
                self.regime_configs[rkey]["shed_count"] = int(new_shed)

        # Clear manual override & cooldown so the user sees immediate response
        self.manual_override_active = False
        self.manual_override_baseline_natural_pct = None
        self.last_on_off_change_utc = None
        self.last_adjustment_utc = datetime.now(timezone.utc)

        self.async_evaluate_room(self.hub.outdoor_state, trigger_control=False)

        # Immediately drive the physical lights to `(new_bri, new_shed)`
        scene_id, scale_dyn, _ = self._resolve_active_scene(reg)
        await self._async_apply_scene_by_id(
            scene_id,
            target_brightness_pct=new_bri,
            target_cct_kelvin=self.result.recommended_cct_kelvin,
            scale_dynamically=scale_dyn,
            shed_count=new_shed,
            transition=2,
        )

        self._update_decision_explanation()
        async_dispatcher_send(
            self.hass, f"{SIGNAL_ROOM_UPDATED}_{self.room_id}"
        )

        if record_ml:
            self._schedule_settled_lux_measurement(
                event_type="nudge_lighter" if wants_more_light else "nudge_darker",
                desired_lights_on=True,
                desired_bri_pct=new_bri,
                shed_count=new_shed,
            )
        else:
            await self.hub.async_save_runtime_state()

    async def async_calibrate_feels_dark_now(self) -> None:
        """Direct calibration: room feels too dark right now -> raise Turn-On Lux Threshold above current lux and turn lights ON."""
        cur_lux = self.result.effective_spatial_lux
        reg = self.hub.outdoor_state.time_regime
        r_weights = self.hub.outdoor_state.regime_weights or {reg: 1.0}

        for rkey, w in r_weights.items():
            if w >= 0.10:
                old_on = float(self.regime_configs[rkey]["turn_on_lux"])
                new_on = max(old_on + 2.0, cur_lux + 2.5)
                self.regime_configs[rkey]["turn_on_lux"] = round(
                    (1.0 - w) * old_on + w * new_on, 1
                )

        self.manual_override_active = False
        self.manual_override_baseline_natural_pct = None
        self.last_on_off_change_utc = None
        self.last_adjustment_utc = None
        self.active_shed_count = 0

        self.async_evaluate_room(self.hub.outdoor_state, trigger_control=False)
        target_bri = max(35, self.result.recommended_brightness_pct)
        scene_id, scale_dyn, _ = self._resolve_active_scene(reg)
        await self._async_apply_scene_by_id(
            scene_id,
            target_brightness_pct=target_bri,
            target_cct_kelvin=self.result.recommended_cct_kelvin,
            scale_dynamically=scale_dyn,
            shed_count=0,
            transition=2,
        )
        self.controlled_lights_are_on = True
        self._update_decision_explanation()
        async_dispatcher_send(self.hass, f"{SIGNAL_ROOM_UPDATED}_{self.room_id}")

        self._schedule_settled_lux_measurement(
            event_type="feels_dark_now",
            desired_lights_on=True,
            desired_bri_pct=target_bri,
            shed_count=0,
        )

    async def async_calibrate_feels_bright_now(self) -> None:
        """Direct calibration: room feels bright enough without lights right now -> turn lights OFF and measure unlit lux to lower Turn-On Threshold."""
        reg = self.hub.outdoor_state.time_regime
        r_weights = self.hub.outdoor_state.regime_weights or {reg: 1.0}
        nat_est = self.result.natural_spatial_lux

        for rkey, w in r_weights.items():
            if w >= 0.10:
                old_on = float(self.regime_configs[rkey]["turn_on_lux"])
                new_on = max(0.5, min(old_on * 0.80, nat_est * 0.78))
                self.regime_configs[rkey]["turn_on_lux"] = round(
                    (1.0 - w) * old_on + w * new_on, 1
                )

        self.manual_override_active = False
        self.manual_override_baseline_natural_pct = None
        self.last_on_off_change_utc = datetime.now(timezone.utc)
        self.active_shed_count = 0
        self.shed_light_entities = []

        rcfg = self.regime_configs.get(reg, DEFAULT_REGIME_SETTINGS[reg])
        off_scene = str(rcfg.get("off_scene", "off"))
        if off_scene != "off" and rcfg.get("off_scene_when_bright", False):
            await self._async_apply_scene_by_id(
                off_scene,
                target_brightness_pct=int(rcfg.get("target_brightness_pct", 35)),
                target_cct_kelvin=self.result.recommended_cct_kelvin,
                scale_dynamically=False,
                shed_count=0,
                transition=2,
            )
            self.off_scene_currently_active = True
        else:
            await self._async_turn_off_room_lights(transition=2)
            self.controlled_lights_are_on = False
            self.off_scene_currently_active = False

        self.async_evaluate_room(self.hub.outdoor_state, trigger_control=False)
        self._schedule_settled_lux_measurement(
            event_type="feels_bright_now",
            desired_lights_on=False,
            desired_bri_pct=0,
            shed_count=0,
        )

    async def async_set_automation_lock(self, locked: bool) -> None:
        """Hard lock or unlock all room lighting and presence automation."""
        self.automation_locked = locked
        if not locked:
            self.manual_override_active = False
            self.manual_override_baseline_natural_pct = None
            self.async_evaluate_room(self.hub.outdoor_state, trigger_control=True)
        else:
            self._update_decision_explanation()
            async_dispatcher_send(
                self.hass, f"{SIGNAL_ROOM_UPDATED}_{self.room_id}"
            )
        await self.hub.async_save_runtime_state()

    async def async_set_window_compass(self, azimuth: float) -> None:
        """Save calibrated window compass heading (0-360°) from the user's phone."""
        self.window_azimuth = round(float(azimuth) % 360.0, 1)
        self.config[CONF_WINDOW_AZIMUTH] = self.window_azimuth
        self.config[CONF_SUN_WINDOW_DIRECTION] = SUN_DIR_COMPASS
        self.async_evaluate_room(self.hub.outdoor_state, trigger_control=True)
        await self.hub.async_save_runtime_state()

    async def async_update_room_config(self, updates: dict[str, Any]) -> None:
        """Update room configuration (light priority order, regime configs, scene triggers, mode, exclusions, etc.) from Sidebar UI."""
        if CONF_OPERATING_MODE in updates:
            self.operating_mode = str(updates[CONF_OPERATING_MODE])
            self.config[CONF_OPERATING_MODE] = self.operating_mode
        if CONF_DAYLIGHT_LEVEL in updates:
            self.config[CONF_DAYLIGHT_LEVEL] = str(updates[CONF_DAYLIGHT_LEVEL])
        if CONF_CCT_MODE in updates:
            self.config[CONF_CCT_MODE] = str(updates[CONF_CCT_MODE])
        if CONF_ON_OFF_COOLDOWN_MINUTES in updates:
            self.config[CONF_ON_OFF_COOLDOWN_MINUTES] = int(updates[CONF_ON_OFF_COOLDOWN_MINUTES])
        if CONF_ADJUST_INTERVAL_MINUTES in updates:
            self.config[CONF_ADJUST_INTERVAL_MINUTES] = int(updates[CONF_ADJUST_INTERVAL_MINUTES])
        if CONF_TRANSITION_SECONDS in updates:
            self.config[CONF_TRANSITION_SECONDS] = int(updates[CONF_TRANSITION_SECONDS])
        if CONF_EXCLUDED_ENTITIES in updates and isinstance(updates[CONF_EXCLUDED_ENTITIES], list):
            self.config[CONF_EXCLUDED_ENTITIES] = list(updates[CONF_EXCLUDED_ENTITIES])
            self.async_discover_area_entities()

        if CONF_LIGHT_PRIORITY_ORDER in updates and isinstance(updates[CONF_LIGHT_PRIORITY_ORDER], list):
            self.light_order_locked = bool(updates.get(CONF_LIGHT_ORDER_LOCKED, True))
            if not self.light_order_locked or not updates[CONF_LIGHT_PRIORITY_ORDER]:
                self.light_order_locked = False
                self._rebuild_light_priority_order()
            else:
                new_order = [str(e) for e in updates[CONF_LIGHT_PRIORITY_ORDER] if e in self.lights]
                remaining = [e for e in self.lights if e not in new_order]
                self.light_priority_order = new_order + remaining
            self.config[CONF_LIGHT_PRIORITY_ORDER] = list(self.light_priority_order)
            self.config[CONF_LIGHT_ORDER_LOCKED] = self.light_order_locked

        if CONF_REGIME_CONFIGS in updates and isinstance(updates[CONF_REGIME_CONFIGS], dict):
            for rkey, rval in updates[CONF_REGIME_CONFIGS].items():
                if rkey in TIME_REGIMES and isinstance(rval, dict):
                    if "occupied_scene" in rval:
                        self.regime_configs[rkey]["occupied_scene"] = str(rval["occupied_scene"])
                    if "off_scene" in rval:
                        self.regime_configs[rkey]["off_scene"] = str(rval["off_scene"])
                    if "off_scene_when_unoccupied" in rval:
                        self.regime_configs[rkey]["off_scene_when_unoccupied"] = bool(rval["off_scene_when_unoccupied"])
                    if "off_scene_when_bright" in rval:
                        self.regime_configs[rkey]["off_scene_when_bright"] = bool(rval["off_scene_when_bright"])
                    if "turn_on_lux" in rval:
                        self.regime_configs[rkey]["turn_on_lux"] = round(clamp(float(rval["turn_on_lux"]), 0.5, 200.0), 1)
                    if "target_brightness_pct" in rval:
                        self.regime_configs[rkey]["target_brightness_pct"] = int(clamp(round(float(rval["target_brightness_pct"])), 1, 100))
                    if "target_total_lux" in rval:
                        self.regime_configs[rkey]["target_total_lux"] = round(clamp(float(rval["target_total_lux"]), 1.0, 300.0), 1)
                    if "shed_count" in rval:
                        self.regime_configs[rkey]["shed_count"] = int(max(0, int(rval["shed_count"])))
                    spec = self.learned_weights.get_regime_spec(rkey)
                    if "illum_model" in rval:
                        spec.illum_model = str(rval["illum_model"])
                    if "weather_model" in rval:
                        spec.weather_model = str(rval["weather_model"])
                    if "season_model" in rval:
                        spec.season_model = str(rval["season_model"])

        if "regime_models" in updates and isinstance(updates["regime_models"], dict):
            for rkey, mval in updates["regime_models"].items():
                if rkey in TIME_REGIMES and isinstance(mval, dict):
                    spec = self.learned_weights.get_regime_spec(rkey)
                    if "illum_model" in mval:
                        spec.illum_model = str(mval["illum_model"])
                    if "weather_model" in mval:
                        spec.weather_model = str(mval["weather_model"])
                    if "season_model" in mval:
                        spec.season_model = str(mval["season_model"])

        if CONF_SCENE_TRIGGERS in updates and isinstance(updates[CONF_SCENE_TRIGGERS], list):
            clean_trigs: list[dict[str, Any]] = []
            for idx, t in enumerate(updates[CONF_SCENE_TRIGGERS]):
                if isinstance(t, dict):
                    teid = str(t.get("entity_id") or "presence_only").strip()
                    raw_states = t.get("states", ["on", "playing"])
                    if isinstance(raw_states, str):
                        states_list = [s.strip() for s in raw_states.split(",") if s.strip()]
                    else:
                        states_list = [str(s).strip() for s in raw_states if str(s).strip()]
                    scale_dyn = bool(t.get("scale_dynamically", t.get("dynamic_scaling", True)))
                    clean_trigs.append(
                        {
                            "id": str(t.get("id") or f"trig_{idx}"),
                            "regime": str(t.get("regime") or "any"),
                            "entity_id": teid,
                            "states": states_list if teid != "presence_only" else ["occupied"],
                            "scene_id": str(t.get("scene_id") or "all_lights"),
                            "scale_dynamically": scale_dyn,
                        }
                    )
            self.scene_triggers = clean_trigs

        self.async_evaluate_room(self.hub.outdoor_state, trigger_control=True)
        await self.hub.async_save_runtime_state()


class PerceptiveLightingHub:
    """Central Hub managing outdoor environment state, regime schedule, historical learning, and all RoomControllers."""

    def __init__(self, hass: HomeAssistant, entry: ConfigEntry) -> None:
        """Initialize the central hub."""
        self.hass = hass
        self.entry = entry
        self.store = Store[dict[str, Any]](hass, STORAGE_VERSION, STORAGE_KEY)
        self.outdoor_state = OutdoorEnvironmentState()
        self.outdoor_profiles: dict[str, SensorLearnedProfile] = {}
        self.regime_schedule: dict[str, Any] = dict(DEFAULT_REGIME_SCHEDULE)
        self.rooms: dict[str, RoomController] = {}
        self.global_lock: bool = False
        self._unsub_listeners: list[Any] = []
        self._persisted_data: dict[str, Any] = {}

    @property
    def weather_entity(self) -> str | None:
        """Return configured weather entity_id."""
        return self._persisted_data.get(
            CONF_WEATHER_ENTITY,
            self.entry.options.get(
                CONF_WEATHER_ENTITY, self.entry.data.get(CONF_WEATHER_ENTITY)
            ),
        )

    @property
    def outdoor_sensors(self) -> list[str]:
        """Return configured outdoor illuminance sensor entity_ids."""
        return list(
            self._persisted_data.get(
                CONF_OUTDOOR_SENSORS,
                self.entry.options.get(
                    CONF_OUTDOOR_SENSORS, self.entry.data.get(CONF_OUTDOOR_SENSORS, [])
                ),
            )
        )

    @property
    def sun_entity(self) -> str:
        """Return sun entity_id."""
        return self.entry.options.get(
            CONF_SUN_ENTITY, self.entry.data.get(CONF_SUN_ENTITY, "sun.sun")
        )

    async def async_setup(self) -> None:
        """Load persisted runtime state, initialize rooms, and bootstrap historical learning."""
        loaded = await self.store.async_load()
        self._persisted_data = loaded if isinstance(loaded, dict) else {}
        self.global_lock = bool(self._persisted_data.get("global_lock", False))

        saved_sched = self._persisted_data.get(
            CONF_REGIME_SCHEDULE,
            self.entry.options.get(CONF_REGIME_SCHEDULE, {}),
        )
        if isinstance(saved_sched, dict):
            self.regime_schedule.update(saved_sched)

        for eid, pdata in self._persisted_data.get("outdoor_profiles", {}).items():
            self.outdoor_profiles[eid] = SensorLearnedProfile.from_dict(pdata)

        rooms_cfg = self.entry.options.get(
            CONF_ROOMS, self.entry.data.get(CONF_ROOMS, {})
        )
        room_persisted_map = self._persisted_data.get("rooms", {})

        for room_id, rcfg in rooms_cfg.items():
            rcfg_copy = dict(rcfg)
            rcfg_copy[CONF_ROOM_ID] = room_id
            room_ctrl = RoomController(
                self, rcfg_copy, room_persisted_map.get(room_id)
            )
            room_ctrl.async_discover_area_entities()
            self.rooms[room_id] = room_ctrl

        # Perform initial calculation
        self.async_update_outdoor_and_rooms(trigger_control=False)

        # Subscribe to state changes & periodic evaluation
        self._unsub_listeners.append(
            self.hass.bus.async_listen(
                EVENT_STATE_CHANGED, self._async_on_state_changed
            )
        )
        self._unsub_listeners.append(
            async_track_time_interval(
                self.hass,
                self._async_periodic_tick,
                timedelta(seconds=60),
            )
        )

        # Schedule deferred background historical Recorder bootstrap learning after startup completes
        async def _deferred_history_learn(_now: datetime) -> None:
            await self.async_run_historical_learning()

        self._unsub_listeners.append(
            async_call_later(self.hass, 10.0, _deferred_history_learn)
        )

    async def async_unload(self) -> None:
        """Clean up listeners on unload."""
        for unsub in self._unsub_listeners:
            unsub()
        self._unsub_listeners.clear()
        await self.async_save_runtime_state()

    async def async_save_runtime_state(self) -> None:
        """Persist room calibrations, adaptive scenes, regime schedule, and learned profiles."""
        data = {
            "global_lock": self.global_lock,
            CONF_WEATHER_ENTITY: self.weather_entity,
            CONF_OUTDOOR_SENSORS: self.outdoor_sensors,
            CONF_REGIME_SCHEDULE: dict(self.regime_schedule),
            "outdoor_profiles": {
                k: v.to_dict() for k, v in self.outdoor_profiles.items()
            },
            "rooms": {
                rid: rctrl.to_persisted_dict()
                for rid, rctrl in self.rooms.items()
            },
        }
        self._persisted_data = data
        await self.store.async_save(data)

    async def async_reset_all_rooms(self) -> None:
        """Reset all rooms and outdoor sensor profiles back to clean v2.0.0 defaults."""
        self.outdoor_profiles = {}
        for room in self.rooms.values():
            await room.async_reset_room(relearn_history=False)
        await self.async_run_historical_learning()

    async def async_update_hub_config(self, updates: dict[str, Any]) -> None:
        """Update Hub-level configuration (Time-of-Day Regime Schedule, outdoor sensors, weather entity, global lock)."""
        if "global_lock" in updates:
            self.global_lock = bool(updates["global_lock"])
        if CONF_WEATHER_ENTITY in updates:
            self._persisted_data[CONF_WEATHER_ENTITY] = updates[CONF_WEATHER_ENTITY]
        if CONF_OUTDOOR_SENSORS in updates and isinstance(updates[CONF_OUTDOOR_SENSORS], list):
            self._persisted_data[CONF_OUTDOOR_SENSORS] = list(updates[CONF_OUTDOOR_SENSORS])
        if CONF_REGIME_SCHEDULE in updates and isinstance(updates[CONF_REGIME_SCHEDULE], dict):
            s_up = updates[CONF_REGIME_SCHEDULE]
            for k in DEFAULT_REGIME_SCHEDULE:
                if k in s_up:
                    if isinstance(DEFAULT_REGIME_SCHEDULE[k], bool):
                        self.regime_schedule[k] = bool(s_up[k])
                    elif isinstance(DEFAULT_REGIME_SCHEDULE[k], (int, float)):
                        self.regime_schedule[k] = float(s_up[k])
                    else:
                        self.regime_schedule[k] = str(s_up[k])

        self.async_update_outdoor_and_rooms(trigger_control=True)
        await self.async_save_runtime_state()

        # Support adding or removing a room directly from the Sidebar UI
        add_area = updates.get("add_area_id")
        remove_room = updates.get("remove_room_id")
        if add_area or remove_room:
            new_opts = dict(self.entry.options or self.entry.data)
            rooms_map = dict(new_opts.get(CONF_ROOMS, {}))
            changed = False
            if add_area and add_area not in rooms_map:
                area_reg = ar.async_get(self.hass)
                area_entry = area_reg.async_get_area(add_area)
                rname = area_entry.name if area_entry else str(add_area)
                rooms_map[add_area] = {
                    CONF_ROOM_ID: add_area,
                    CONF_ROOM_NAME: rname,
                    CONF_AREA_ID: add_area,
                    CONF_OPERATING_MODE: updates.get(CONF_OPERATING_MODE, MODE_FULL_ADAPTIVE),
                    CONF_ACTIVITIES: [ACTIVITY_RELAXING, "working", "reading", "film"],
                    CONF_DEFAULT_ACTIVITY: ACTIVITY_RELAXING,
                    CONF_DAYLIGHT_LEVEL: DAYLIGHT_MEDIUM,
                }
                changed = True
            if remove_room and remove_room in rooms_map:
                del rooms_map[remove_room]
                changed = True
            if changed:
                new_opts[CONF_ROOMS] = rooms_map
                self.hass.config_entries.async_update_entry(self.entry, options=new_opts)

    async def async_run_historical_learning(self) -> None:
        """Bootstrap outdoor and indoor sensor profiles from Home Assistant Recorder history."""
        if self.outdoor_sensors:
            self.outdoor_profiles = await async_learn_sensor_profiles_from_history(
                self.hass,
                self.outdoor_sensors,
                self.outdoor_profiles,
                days_back=10,
                is_outdoor=True,
            )

        for room in self.rooms.values():
            if room.lux_sensors:
                room.sensor_profiles = await async_learn_sensor_profiles_from_history(
                    self.hass,
                    room.lux_sensors,
                    room.sensor_profiles,
                    days_back=10,
                    is_outdoor=False,
                )

        self.async_update_outdoor_and_rooms(trigger_control=False)
        await self.async_save_runtime_state()

    @callback
    def async_update_outdoor_and_rooms(self, trigger_control: bool = True) -> None:
        """Read outdoor sensors, weather, temperature, photoperiod, and sun, then update all rooms."""
        outdoor_readings: dict[str, float] = {}
        for eid in self.outdoor_sensors:
            st = self.hass.states.get(eid)
            if st and st.state not in (STATE_UNKNOWN, STATE_UNAVAILABLE):
                try:
                    val = float(st.state)
                    if val >= 0.0:
                        outdoor_readings[eid] = val
                except (ValueError, TypeError):
                    pass

        weather_cond: str | None = None
        cloud_cov: float | None = None
        outdoor_temp_c: float | None = None
        if self.weather_entity:
            wst = self.hass.states.get(self.weather_entity)
            if wst and wst.state not in (STATE_UNKNOWN, STATE_UNAVAILABLE):
                weather_cond = wst.state
                cloud_cov = wst.attributes.get("cloud_coverage")
                raw_temp = wst.attributes.get("temperature")
                if raw_temp is not None:
                    try:
                        tval = float(raw_temp)
                        t_unit = wst.attributes.get("temperature_unit", "°C")
                        if t_unit == "°F":
                            tval = (tval - 32.0) * 5.0 / 9.0
                        outdoor_temp_c = tval
                    except (ValueError, TypeError):
                        pass

        sol_el = 15.0
        sol_az = 180.0
        sun_st = self.hass.states.get(self.sun_entity)
        if sun_st:
            sol_el = float(sun_st.attributes.get("elevation", 15.0))
            sol_az = float(sun_st.attributes.get("azimuth", 180.0))

        now_local = dt_util.now()
        local_hour = now_local.hour + now_local.minute / 60.0
        lat = float(getattr(self.hass.config, "latitude", 51.5))
        day_len = estimate_day_length_hours(lat, now_local.timetuple().tm_yday)

        self.outdoor_state = compute_outdoor_environment(
            sensor_readings=outdoor_readings,
            sensor_profiles=self.outdoor_profiles,
            weather_condition=weather_cond,
            weather_cloud_coverage=cloud_cov,
            solar_elevation=sol_el,
            solar_azimuth=sol_az,
            outdoor_temp_c=outdoor_temp_c,
            day_length_hours=day_len,
            local_hour=local_hour,
            regime_schedule=self.regime_schedule,
        )

        async_dispatcher_send(self.hass, SIGNAL_HUB_UPDATED)

        for room in self.rooms.values():
            room.async_evaluate_room(
                self.outdoor_state, trigger_control=trigger_control
            )

    @callback
    def _async_on_state_changed(self, event: Event) -> None:
        """React to state changes on outdoor sensors, weather, sun, or room entities."""
        entity_id = event.data.get("entity_id")
        if not entity_id:
            return

        if (
            entity_id in self.outdoor_sensors
            or entity_id == self.weather_entity
            or entity_id == self.sun_entity
        ):
            self.async_update_outdoor_and_rooms(trigger_control=True)
            return

        for room in self.rooms.values():
            if entity_id in room.lights:
                room.async_handle_light_state_event(event)
                room.async_evaluate_room(
                    self.outdoor_state, trigger_control=False
                )
            else:
                trigger_eids = {t.get("entity_id") for t in room.scene_triggers if t.get("entity_id")}
                if (
                    entity_id in room.presence_sensors
                    or entity_id in room.media_players
                    or entity_id in room.lux_sensors
                    or entity_id in room.temp_sensors
                    or entity_id in room.humidity_sensors
                    or entity_id in trigger_eids
                    or entity_id in room.config.get(CONF_BLOCKER_ENTITIES, [])
                ):
                    room.async_evaluate_room(
                        self.outdoor_state, trigger_control=True
                    )

    @callback
    def _async_periodic_tick(self, _now: datetime) -> None:
        """Periodic 60-second tick to re-discover Area entities and run smooth adjustments."""
        for room in self.rooms.values():
            room.async_discover_area_entities()
        self.async_update_outdoor_and_rooms(trigger_control=True)
