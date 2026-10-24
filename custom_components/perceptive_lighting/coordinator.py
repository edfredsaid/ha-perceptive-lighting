"""Room Coordinator & Adaptive Lighting Controller for Perceptive Lighting."""
from __future__ import annotations

import asyncio
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
from homeassistant.helpers.event import async_track_time_interval
from homeassistant.helpers.storage import Store

from .const import (
    ACTIVITY_DEFINITIONS,
    ACTIVITY_RELAXING,
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
    CONF_LINKED_HA_SCENES,
    CONF_ON_OFF_COOLDOWN_MINUTES,
    CONF_OPERATING_MODE,
    CONF_OUTDOOR_SENSOR_PROFILES,
    CONF_OUTDOOR_SENSORS,
    CONF_ROOM_ID,
    CONF_ROOM_NAME,
    CONF_ROOMS,
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
    DEFAULT_SUBSTANTIAL_LUX_SHIFT_PCT,
    DEFAULT_TRANSITION_SECONDS,
    DOMAIN,
    MODE_FULL_ADAPTIVE,
    MODE_SENSOR_ONLY,
    MODE_SIMPLE_TRANSIT,
    SUN_DIR_AUTO,
    SUN_DIR_COMPASS,
)
from .engine import (
    OutdoorEnvironmentState,
    RoomPerceptiveResult,
    SensorLearnedProfile,
    clamp,
    compute_outdoor_environment,
    compute_room_perceptive_illuminance,
)
from .history_learner import async_learn_sensor_profiles_from_history

_LOGGER = logging.getLogger(__name__)

SIGNAL_ROOM_UPDATED = f"{DOMAIN}_room_updated"
SIGNAL_HUB_UPDATED = f"{DOMAIN}_hub_updated"
STORAGE_VERSION = 1
STORAGE_KEY = f"{DOMAIN}_runtime_state"


@dataclass
class AdaptiveSceneSpec:
    """Represents a calibrated or HA-linked Adaptive Scene for a room."""

    scene_id: str
    name: str
    activity: str = ACTIVITY_RELAXING
    reference_deficit_pct: float = 65.0
    linked_ha_scene_entity: str | None = None
    # entity_id -> {"state": "on"|"off", "brightness_pct": int, "color_temp_kelvin": int|None, "rgb_color": list|None}
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
            scene_id=data.get("scene_id", ""),
            name=data.get("name", "Default"),
            activity=data.get("activity", ACTIVITY_RELAXING),
            reference_deficit_pct=float(data.get("reference_deficit_pct", 65.0)),
            linked_ha_scene_entity=data.get("linked_ha_scene_entity"),
            light_states=dict(data.get("light_states", {})),
        )


class RoomController:
    """Manages perceptive calculation, Area entity discovery, and adaptive lighting for a single room."""

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
        self.room_name: str = room_config[CONF_ROOM_NAME]
        self.area_id: str | None = room_config.get(CONF_AREA_ID)
        self.operating_mode: str = room_config.get(
            CONF_OPERATING_MODE, MODE_FULL_ADAPTIVE
        )

        # Discovered Area Entities (excluding blacklist)
        self.lux_sensors: list[str] = []
        self.presence_sensors: list[str] = []
        self.media_players: list[str] = []
        self.lights: list[str] = []
        self.all_area_entities: dict[str, list[str]] = {}

        # Per-sensor learned/configured profiles
        self.sensor_profiles: dict[str, SensorLearnedProfile] = {}
        for eid, pdata in room_config.get(CONF_SENSOR_PROFILES, {}).items():
            if isinstance(pdata, dict):
                self.sensor_profiles[eid] = SensorLearnedProfile.from_dict(pdata)
            elif isinstance(pdata, str):
                self.sensor_profiles[eid] = SensorLearnedProfile(
                    entity_id=eid, placement=pdata
                )

        # Persisted runtime state (nudges, lock, captured scenes, learned light lux)
        pstate = persisted_state or {}
        self.automation_locked: bool = bool(pstate.get("automation_locked", False))
        self.feel_nudge_day_pct: float = float(pstate.get("feel_nudge_day_pct", 0.0))
        self.feel_nudge_night_pct: float = float(
            pstate.get("feel_nudge_night_pct", 0.0)
        )
        self.window_azimuth: float | None = pstate.get(
            "window_azimuth", room_config.get(CONF_WINDOW_AZIMUTH)
        )
        self.active_activity: str = pstate.get(
            "active_activity",
            room_config.get(CONF_DEFAULT_ACTIVITY, ACTIVITY_RELAXING),
        )
        self.active_scene_id: str = pstate.get("active_scene_id", "default")
        self.max_artificial_lux: float = float(
            pstate.get("max_artificial_lux", 110.0)
        )

        # Adaptive scenes (built-in activity scenes + linked HA scenes + user captured scenes)
        self.adaptive_scenes: dict[str, AdaptiveSceneSpec] = {}
        for sid, sdata in pstate.get("adaptive_scenes", {}).items():
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
        self.manual_override_active: bool = False
        self.manual_override_baseline_natural_pct: float | None = None
        self.last_on_off_change_utc: datetime | None = None
        self.last_adjustment_utc: datetime | None = None
        self.controlled_lights_are_on: bool = False
        self.pre_light_natural_lux: float | None = None
        self._recent_context_ids: set[str] = set()

    @property
    def excluded_entities(self) -> set[str]:
        """Return set of blacklisted entity_ids for this room."""
        return set(self.config.get(CONF_EXCLUDED_ENTITIES, []))

    @property
    def cooldown_active(self) -> bool:
        """Return True if the 10-minute on/off anti-flicker lockout is active."""
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
            "automation_locked": self.automation_locked,
            "feel_nudge_day_pct": round(self.feel_nudge_day_pct, 2),
            "feel_nudge_night_pct": round(self.feel_nudge_night_pct, 2),
            "window_azimuth": self.window_azimuth,
            "active_activity": self.active_activity,
            "active_scene_id": self.active_scene_id,
            "max_artificial_lux": round(self.max_artificial_lux, 1),
            "adaptive_scenes": {
                k: v.to_dict() for k, v in self.adaptive_scenes.items()
            },
            "sensor_profiles": {
                k: v.to_dict() for k, v in self.sensor_profiles.items()
            },
        }

    @callback
    def async_discover_area_entities(self) -> None:
        """Dynamically discover all relevant entities in the room's HA Area, minus exclusions."""
        if not self.area_id:
            return

        ent_reg = er.async_get(self.hass)
        dev_reg = dr.async_get(self.hass)

        # Find all devices in this area
        area_device_ids = {
            dev.id
            for dev in dev_reg.devices.values()
            if dev.area_id == self.area_id and not dev.disabled_by
        }

        lux_list: list[str] = []
        pres_list: list[str] = []
        media_list: list[str] = []
        light_list: list[str] = []

        for entry in ent_reg.entities.values():
            if entry.disabled_by:
                continue
            # Exclude our own Perceptive Lighting entities!
            if entry.platform == DOMAIN:
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

            if domain == "sensor" and (
                dev_class == "illuminance" or unit in ("lx", "lm/m²")
            ):
                lux_list.append(eid)
            elif domain == "binary_sensor" and dev_class in (
                "motion",
                "occupancy",
                "presence",
            ):
                pres_list.append(eid)
            elif domain == "media_player":
                media_list.append(eid)
            elif domain == "light":
                light_list.append(eid)

        self.all_area_entities = {
            "illuminance_sensors": sorted(lux_list),
            "presence_sensors": sorted(pres_list),
            "media_players": sorted(media_list),
            "lights": sorted(light_list),
        }

        excluded = self.excluded_entities
        self.lux_sensors = [e for e in self.all_area_entities["illuminance_sensors"] if e not in excluded]
        self.presence_sensors = [e for e in self.all_area_entities["presence_sensors"] if e not in excluded]
        self.media_players = (
            [e for e in self.all_area_entities["media_players"] if e not in excluded]
            if self.config.get(CONF_INCLUDE_MEDIA_PLAYERS, True)
            else []
        )
        self.lights = [e for e in self.all_area_entities["lights"] if e not in excluded]

        # Sync linked HA scenes from config or area
        linked_scenes = list(self.config.get(CONF_LINKED_HA_SCENES, []))
        for entry in ent_reg.entities.values():
            if entry.domain == "scene" and not entry.disabled_by:
                in_area = entry.area_id == self.area_id or (
                    entry.area_id is None and entry.device_id in area_device_ids
                )
                if in_area and entry.entity_id not in linked_scenes and entry.entity_id not in excluded:
                    linked_scenes.append(entry.entity_id)

        for scene_eid in linked_scenes:
            sid = f"ha_{scene_eid.replace('scene.', '')}"
            if sid not in self.adaptive_scenes:
                st = self.hass.states.get(scene_eid)
                friendly = (
                    st.attributes.get("friendly_name", scene_eid) if st else scene_eid
                )
                # Strip room name prefix if present for cleaner pill labels
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
        """Guess the best perceptual activity curve from a scene name."""
        low = name.lower()
        if any(w in low for w in ("work", "meeting", "study", "office", "desk", "daylight")):
            return "working"
        if any(w in low for w in ("movie", "film", "cinema", "projector", "tv")):
            return "film"
        if any(w in low for w in ("read", "book")):
            return "reading"
        if any(w in low for w in ("red", "night", "bed", "sleep")):
            return "sleep_prep"
        if any(w in low for w in ("ultra", "bright")):
            return "bright_task"
        return ACTIVITY_RELAXING

    def get_available_scene_options(self) -> dict[str, str]:
        """Return mapping of scene_id -> human label for UI and Select entity."""
        options: dict[str, str] = {"default": "Auto (Activity Default)"}
        # Include enabled activities as built-in adaptive modes
        enabled_acts = self.config.get(
            CONF_ACTIVITIES, list(ACTIVITY_DEFINITIONS.keys())
        )
        for act_key in enabled_acts:
            if act_key in ACTIVITY_DEFINITIONS:
                options[f"activity_{act_key}"] = ACTIVITY_DEFINITIONS[act_key]["name"]

        # Include captured and HA-linked adaptive scenes
        for sid, spec in self.adaptive_scenes.items():
            options[sid] = f"Scene: {spec.name}"
        return options

    @callback
    def async_evaluate_room(
        self, outdoor: OutdoorEnvironmentState, trigger_control: bool = False
    ) -> None:
        """Recalculate room Perceptive Illuminance and optionally run controller logic."""
        # 1. Read & smooth indoor lux sensors (EMA smoothing to filter brief cloud spikes)
        current_readings: dict[str, float] = {}
        for eid in self.lux_sensors:
            st = self.hass.states.get(eid)
            if st and st.state not in (STATE_UNKNOWN, STATE_UNAVAILABLE):
                try:
                    raw_val = float(st.state)
                    prev = self.smoothed_indoor_lux.get(eid, raw_val)
                    # Alpha = 0.35 provides responsive yet cloud-dampened smoothing
                    smoothed = 0.35 * raw_val + 0.65 * prev
                    self.smoothed_indoor_lux[eid] = smoothed
                    current_readings[eid] = smoothed
                except (ValueError, TypeError):
                    pass

        # 2. Estimate artificial light contribution from active room lights
        active_brightness_ratios: list[float] = []
        any_light_on = False
        for light_eid in self.lights:
            lst = self.hass.states.get(light_eid)
            if lst and lst.state == STATE_ON:
                any_light_on = True
                bri = lst.attributes.get("brightness")
                ratio = (float(bri) / 255.0) if bri is not None else 0.8
                active_brightness_ratios.append(ratio)

        self.controlled_lights_are_on = any_light_on
        if any_light_on and active_brightness_ratios:
            avg_bri_ratio = sum(active_brightness_ratios) / len(self.lights)
            # Non-linear lumen output estimation
            artificial_lux_est = self.max_artificial_lux * math.pow(avg_bri_ratio, 1.4)
        else:
            artificial_lux_est = 0.0

        # 3. Check activity auto-triggers (e.g., TV playing -> film, PC on -> working)
        self._check_activity_triggers()

        # 4. Run psychophysical calculation engine
        sun_dir = self.config.get(CONF_SUN_WINDOW_DIRECTION, SUN_DIR_AUTO)
        if self.window_azimuth is not None and sun_dir == SUN_DIR_AUTO:
            sun_dir = SUN_DIR_COMPASS

        prev_natural_pct = self.result.natural_perceptive_illuminance_pct
        self.result = compute_room_perceptive_illuminance(
            indoor_sensor_readings=current_readings,
            sensor_profiles=self.sensor_profiles,
            outdoor=outdoor,
            activity_key=self.active_activity,
            daylight_level=self.config.get(CONF_DAYLIGHT_LEVEL, DAYLIGHT_MEDIUM),
            sun_window_direction=sun_dir,
            window_azimuth=self.window_azimuth,
            artificial_lux_estimate=artificial_lux_est,
            feel_nudge_day_pct=self.feel_nudge_day_pct,
            feel_nudge_night_pct=self.feel_nudge_night_pct,
        )

        # 5. Evaluate occupancy (presence sensors + optional media players)
        was_occupied = self.is_occupied
        self.is_occupied = self._compute_occupancy()

        # 6. Check Manual Override reset conditions:
        #    Resets if room becomes unoccupied OR if a substantial shift in natural illuminance occurs
        if self.manual_override_active:
            if not self.is_occupied and was_occupied:
                _LOGGER.debug(
                    "[%s] Room vacated; clearing manual override.", self.room_name
                )
                self.manual_override_active = False
                self.manual_override_baseline_natural_pct = None
            elif self.manual_override_baseline_natural_pct is not None:
                shift = abs(
                    self.result.natural_perceptive_illuminance_pct
                    - self.manual_override_baseline_natural_pct
                )
                if shift >= DEFAULT_SUBSTANTIAL_LUX_SHIFT_PCT:
                    _LOGGER.debug(
                        "[%s] Substantial natural illuminance shift (%.1f%%); clearing manual override.",
                        self.room_name,
                        shift,
                    )
                    self.manual_override_active = False
                    self.manual_override_baseline_natural_pct = None

        # Notify entities and custom Lovelace cards
        async_dispatcher_send(
            self.hass, f"{SIGNAL_ROOM_UPDATED}_{self.room_id}"
        )

        # 7. Execute controller actions if in controller mode
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
        """Return True if any presence sensor or active media player indicates occupancy."""
        for peid in self.presence_sensors:
            st = self.hass.states.get(peid)
            if st and st.state == STATE_ON:
                return True

        for meid in self.media_players:
            st = self.hass.states.get(meid)
            if st and st.state in (STATE_PLAYING, "paused", "buffering"):
                return True

        return False

    def _check_activity_triggers(self) -> None:
        """Switch active activity automatically if configured trigger entities match."""
        triggers: dict[str, str] = self.config.get(CONF_ACTIVITY_TRIGGERS, {})
        for entity_id, target_activity in triggers.items():
            st = self.hass.states.get(entity_id)
            if st and st.state in (STATE_ON, STATE_PLAYING):
                if target_activity in ACTIVITY_DEFINITIONS:
                    self.active_activity = target_activity
                    return

    def _is_blocked_by_inhibit_entity(self) -> bool:
        """Return True if any configured blocker entity is currently ON."""
        for beid in self.config.get(CONF_BLOCKER_ENTITIES, []):
            st = self.hass.states.get(beid)
            if st and st.state == STATE_ON:
                return True
        return False

    async def _async_run_lighting_controller(
        self, occupancy_just_changed: bool = False, force_apply: bool = False
    ) -> None:
        """Execute presence & perceptive deficit lighting control with anti-flicker guards."""
        if self.operating_mode == MODE_SENSOR_ONLY:
            return
        if (
            self.automation_locked
            or self.hub.global_lock
            or self._is_blocked_by_inhibit_entity()
        ):
            return
        if self.manual_override_active and not force_apply:
            return

        now = datetime.now(timezone.utc)

        # Case 1: Room is unoccupied -> Turn off controlled lights
        if not self.is_occupied and not force_apply:
            if self.controlled_lights_are_on:
                await self._async_turn_off_room_lights()
                self.last_on_off_change_utc = now
            return

        # Case 2: Room is occupied (or force_apply from scene selector)
        deficit = self.result.lighting_deficit_pct
        rec_bri = self.result.recommended_brightness_pct
        rec_cct = self.result.recommended_cct_kelvin

        # Should lights be ON or OFF based on perceptive deficit?
        wants_lights_on = deficit >= 18.0 and rec_bri > 0
        wants_lights_off = deficit <= 5.0

        if not self.controlled_lights_are_on:
            # Lights are currently OFF
            if wants_lights_on or force_apply:
                # Check 10-minute on/off lockout (unless presence just entered the room or user forced scene)
                if (
                    not occupancy_just_changed
                    and not force_apply
                    and self.cooldown_active
                ):
                    return
                await self._async_apply_active_scene_or_groups(
                    rec_bri, rec_cct, transition=3 if occupancy_just_changed else 15
                )
                self.last_on_off_change_utc = now
                self.last_adjustment_utc = now
        else:
            # Lights are currently ON while room is occupied
            if wants_lights_off and not force_apply:
                # Only turn lights off while occupied if 10-minute cooldown has passed
                if self.cooldown_active:
                    return
                await self._async_turn_off_room_lights(transition=25)
                self.last_on_off_change_utc = now
                return

            # Lights stay ON -> Check 5-minute gradual adjustment interval
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
            await self._async_apply_active_scene_or_groups(
                rec_bri,
                rec_cct,
                transition=2 if force_apply else transition_sec,
            )
            self.last_adjustment_utc = now

    async def _async_apply_active_scene_or_groups(
        self, target_brightness_pct: int, target_cct_kelvin: int, transition: int = 15
    ) -> None:
        """Apply either the selected Calibrated Adaptive Scene or Light Group scaling."""
        cct_mode = self.config.get(CONF_CCT_MODE, CCT_EXTERNAL)
        apply_cct = cct_mode == CCT_KRUITHOF

        # Check if an Adaptive Scene is selected
        spec = self.adaptive_scenes.get(self.active_scene_id)
        if spec:
            await self._async_apply_calibrated_scene(
                spec,
                target_brightness_pct=target_brightness_pct,
                target_cct_kelvin=target_cct_kelvin if apply_cct else None,
                transition=transition,
            )
            return

        # Default Light Groups scaling (or all room lights if no custom groups defined)
        groups = self.config.get(CONF_LIGHT_GROUPS, [])
        if not groups:
            groups = [
                {
                    "name": "Room Lights",
                    "entities": self.lights,
                    "min_deficit_pct": 15.0,
                    "brightness_weight": 1.0,
                }
            ]

        deficit = self.result.lighting_deficit_pct
        for grp in groups:
            g_entities = [
                e for e in grp.get("entities", []) if e not in self.excluded_entities
            ]
            if not g_entities:
                continue
            min_def = float(grp.get("min_deficit_pct", 15.0))
            weight = float(grp.get("brightness_weight", 1.0))

            if deficit < min_def:
                # Turn off this tier if it's currently on
                on_ents = [
                    e
                    for e in g_entities
                    if (st := self.hass.states.get(e)) and st.state == STATE_ON
                ]
                if on_ents:
                    await self._async_call_light_service(
                        STATE_OFF, on_ents, {"transition": transition}
                    )
            else:
                scaled_bri = int(clamp(round(target_brightness_pct * weight), 5, 100))
                service_data: dict[str, Any] = {
                    "brightness_pct": scaled_bri,
                    "transition": transition,
                }
                if apply_cct:
                    service_data["color_temp_kelvin"] = target_cct_kelvin
                await self._async_call_light_service(
                    STATE_ON, g_entities, service_data
                )

    async def _async_apply_calibrated_scene(
        self,
        spec: AdaptiveSceneSpec,
        target_brightness_pct: int,
        target_cct_kelvin: int | None,
        transition: int = 15,
    ) -> None:
        """Apply an Adaptive Scene scaled dynamically to the current lighting deficit."""
        deficit = max(15.0, self.result.lighting_deficit_pct)
        ref_def = max(15.0, spec.reference_deficit_pct)
        # Scale ratio relative to the deficit when the scene was calibrated
        scale_ratio = clamp(math.pow(deficit / ref_def, 0.75), 0.25, 1.65)

        if spec.light_states:
            to_turn_off: list[str] = []
            for eid, lstate in spec.light_states.items():
                if eid in self.excluded_entities:
                    continue
                if lstate.get("state") == STATE_OFF:
                    to_turn_off.append(eid)
                else:
                    base_bri = float(lstate.get("brightness_pct", target_brightness_pct))
                    adapted_bri = int(clamp(round(base_bri * scale_ratio), 5, 100))
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

            if to_turn_off:
                await self._async_call_light_service(
                    STATE_OFF, to_turn_off, {"transition": transition}
                )
            return

        # If linked to a native HA scene entity without captured per-light snapshot yet:
        # Activate the HA scene, then capture its light ratios and scale brightness!
        if spec.linked_ha_scene_entity:
            ctx = self._make_context()
            await self.hass.services.async_call(
                "scene",
                "turn_on",
                {
                    ATTR_ENTITY_ID: spec.linked_ha_scene_entity,
                    "transition": min(transition, 5),
                },
                context=ctx,
            )
            await asyncio.sleep(0.6)
            # Snapshot the resulting light states so future adjustments scale smoothly
            self._snapshot_current_lights_into_scene(spec)
            if abs(scale_ratio - 1.0) > 0.12:
                for eid, lstate in spec.light_states.items():
                    if lstate.get("state") == STATE_ON:
                        base_bri = float(
                            lstate.get("brightness_pct", target_brightness_pct)
                        )
                        adapted_bri = int(
                            clamp(round(base_bri * scale_ratio), 5, 100)
                        )
                        await self._async_call_light_service(
                            STATE_ON,
                            [eid],
                            {
                                "brightness_pct": adapted_bri,
                                "transition": transition,
                            },
                        )

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
                    "brightness_pct": max(5, bri_pct),
                    "color_temp_kelvin": st.attributes.get("color_temp_kelvin"),
                    "rgb_color": st.attributes.get("rgb_color"),
                }
            else:
                snapshot[eid] = {"state": STATE_OFF, "brightness_pct": 0}
        spec.light_states = snapshot

    async def _async_turn_off_room_lights(self, transition: int = 10) -> None:
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
        """Call light.turn_on or light.turn_off with tracked context ID to avoid false manual override."""
        if not entity_ids:
            return
        ctx = self._make_context()
        service = "turn_on" if action == STATE_ON else "turn_off"
        payload = {ATTR_ENTITY_ID: entity_ids}
        # Only pass transition/brightness to lights that support it
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
            # Prune oldest context IDs
            while len(self._recent_context_ids) > 60:
                self._recent_context_ids.pop()
        return ctx

    @callback
    def async_handle_light_state_event(self, event: Event) -> None:
        """Detect manual light adjustments (wall switch, remote, dashboard) to engage soft Manual Override."""
        if self.operating_mode == MODE_SENSOR_ONLY or self.automation_locked:
            return

        new_state = event.data.get("new_state")
        old_state = event.data.get("old_state")
        if new_state is None or old_state is None:
            return
        if new_state.state in (STATE_UNKNOWN, STATE_UNAVAILABLE):
            return
        if old_state.state in (STATE_UNKNOWN, STATE_UNAVAILABLE):
            return

        # Ignore our own automated service calls!
        ctx = event.context
        if (
            ctx.id in self._recent_context_ids
            or ctx.parent_id in self._recent_context_ids
        ):
            return

        # Check if on/off state or brightness actually changed
        old_bri = old_state.attributes.get("brightness")
        new_bri = new_state.attributes.get("brightness")
        state_changed = old_state.state != new_state.state
        bri_changed = (
            old_bri is not None
            and new_bri is not None
            and abs(int(old_bri) - int(new_bri)) > 8
        )

        if state_changed or bri_changed:
            _LOGGER.debug(
                "[%s] Manual light change detected on %s; engaging Manual Override.",
                self.room_name,
                new_state.entity_id,
            )
            self.manual_override_active = True
            self.manual_override_baseline_natural_pct = (
                self.result.natural_perceptive_illuminance_pct
            )
            async_dispatcher_send(
                self.hass, f"{SIGNAL_ROOM_UPDATED}_{self.room_id}"
            )

    # --- User Actions & Calibration Methods ---

    async def async_select_scene_or_activity(self, option_key: str) -> None:
        """Switch the room's active Adaptive Scene or Activity and apply immediately."""
        self.manual_override_active = False
        self.manual_override_baseline_natural_pct = None

        if option_key == "default":
            self.active_scene_id = "default"
            self.active_activity = self.config.get(
                CONF_DEFAULT_ACTIVITY, ACTIVITY_RELAXING
            )
        elif option_key.startswith("activity_"):
            act = option_key.replace("activity_", "", 1)
            if act in ACTIVITY_DEFINITIONS:
                self.active_activity = act
                self.active_scene_id = f"activity_{act}"
        elif option_key in self.adaptive_scenes:
            spec = self.adaptive_scenes[option_key]
            self.active_scene_id = option_key
            self.active_activity = spec.activity
        else:
            # Match by scene friendly name
            for sid, spec in self.adaptive_scenes.items():
                if spec.name.lower() == option_key.lower():
                    self.active_scene_id = sid
                    self.active_activity = spec.activity
                    break

        self.async_evaluate_room(self.hub.outdoor_state, trigger_control=False)
        await self._async_run_lighting_controller(force_apply=True)
        await self.hub.async_save_runtime_state()

    async def async_capture_current_as_adaptive_scene(
        self, name: str, activity: str | None = None
    ) -> AdaptiveSceneSpec:
        """Snapshot the room's current light states & current deficit as a reusable Adaptive Scene."""
        slug = "".join(c if c.isalnum() else "_" for c in name.lower()).strip("_")
        scene_id = f"captured_{slug}"
        act = activity if activity in ACTIVITY_DEFINITIONS else self.active_activity
        ref_deficit = max(20.0, self.result.lighting_deficit_pct)

        spec = AdaptiveSceneSpec(
            scene_id=scene_id,
            name=name,
            activity=act,
            reference_deficit_pct=ref_deficit,
        )
        self._snapshot_current_lights_into_scene(spec)
        self.adaptive_scenes[scene_id] = spec
        self.active_scene_id = scene_id
        self.manual_override_active = False

        await self.hub.async_save_runtime_state()
        async_dispatcher_send(
            self.hass, f"{SIGNAL_ROOM_UPDATED}_{self.room_id}"
        )
        return spec

    async def async_nudge_feel(self, direction: str, step_pct: float = DEFAULT_MICRO_NUDGE_STEP_PCT) -> None:
        """Nudge the room's perceptual target slightly lighter (+more light) or darker (-too bright)."""
        delta = abs(step_pct) if direction in ("lighter", "more_light", "darker_room", "up", "+") else -abs(step_pct)
        if self.hub.outdoor_state.is_daytime:
            self.feel_nudge_day_pct = clamp(self.feel_nudge_day_pct + delta, -50.0, 50.0)
        else:
            self.feel_nudge_night_pct = clamp(self.feel_nudge_night_pct + delta, -50.0, 50.0)

        self.async_evaluate_room(self.hub.outdoor_state, trigger_control=True)
        await self.hub.async_save_runtime_state()

    async def async_calibrate_feels_dark_now(self) -> None:
        """One-tap teaching calibration: adjust target so current natural lux registers as needing light."""
        # Boost target so natural perceptive illuminance drops to ~58% (Dim / needing light)
        if self.result.natural_perceptive_illuminance_pct > 58.0:
            needed_steps = (self.result.natural_perceptive_illuminance_pct - 55.0) * 0.65
            await self.async_nudge_feel("more_light", step_pct=clamp(needed_steps, 4.0, 24.0))
        else:
            await self.async_nudge_feel("more_light", step_pct=4.0)

    async def async_set_automation_lock(self, locked: bool) -> None:
        """Hard lock or unlock all room lighting and presence automation."""
        self.automation_locked = locked
        if not locked:
            self.manual_override_active = False
            self.manual_override_baseline_natural_pct = None
            self.async_evaluate_room(self.hub.outdoor_state, trigger_control=True)
        else:
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


class PerceptiveLightingHub:
    """Central Hub managing outdoor environment state, historical learning, and all RoomControllers."""

    def __init__(self, hass: HomeAssistant, entry: ConfigEntry) -> None:
        """Initialize the central hub."""
        self.hass = hass
        self.entry = entry
        self.store = Store[dict[str, Any]](hass, STORAGE_VERSION, STORAGE_KEY)
        self.outdoor_state = OutdoorEnvironmentState()
        self.outdoor_profiles: dict[str, SensorLearnedProfile] = {}
        self.rooms: dict[str, RoomController] = {}
        self.global_lock: bool = False
        self._unsub_listeners: list[Any] = []
        self._persisted_data: dict[str, Any] = {}

    @property
    def weather_entity(self) -> str | None:
        """Return configured weather entity_id."""
        return self.entry.options.get(
            CONF_WEATHER_ENTITY, self.entry.data.get(CONF_WEATHER_ENTITY)
        )

    @property
    def outdoor_sensors(self) -> list[str]:
        """Return configured outdoor illuminance sensor entity_ids."""
        return list(
            self.entry.options.get(
                CONF_OUTDOOR_SENSORS, self.entry.data.get(CONF_OUTDOOR_SENSORS, [])
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

        for eid, pdata in self._persisted_data.get("outdoor_profiles", {}).items():
            self.outdoor_profiles[eid] = SensorLearnedProfile.from_dict(pdata)

        # Initialize configured rooms
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

        # Schedule non-blocking historical Recorder bootstrap learning
        self.hass.async_create_task(self.async_run_historical_learning())

    async def async_unload(self) -> None:
        """Clean up listeners on unload."""
        for unsub in self._unsub_listeners:
            unsub()
        self._unsub_listeners.clear()
        await self.async_save_runtime_state()

    async def async_save_runtime_state(self) -> None:
        """Persist room calibrations, adaptive scenes, and learned profiles."""
        data = {
            "global_lock": self.global_lock,
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
        """Read outdoor sensors, weather, and sun, then update all rooms."""
        # 1. Read outdoor illuminance sensors
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

        # 2. Read weather entity
        weather_cond: str | None = None
        cloud_cov: float | None = None
        if self.weather_entity:
            wst = self.hass.states.get(self.weather_entity)
            if wst and wst.state not in (STATE_UNKNOWN, STATE_UNAVAILABLE):
                weather_cond = wst.state
                cloud_cov = wst.attributes.get("cloud_coverage")

        # 3. Read sun entity
        sol_el = 15.0
        sol_az = 180.0
        sun_st = self.hass.states.get(self.sun_entity)
        if sun_st:
            sol_el = float(sun_st.attributes.get("elevation", 15.0))
            sol_az = float(sun_st.attributes.get("azimuth", 180.0))

        self.outdoor_state = compute_outdoor_environment(
            sensor_readings=outdoor_readings,
            sensor_profiles=self.outdoor_profiles,
            weather_condition=weather_cond,
            weather_cloud_coverage=cloud_cov,
            solar_elevation=sol_el,
            solar_azimuth=sol_az,
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
            elif (
                entity_id in room.presence_sensors
                or entity_id in room.media_players
                or entity_id in room.lux_sensors
                or entity_id in room.config.get(CONF_ACTIVITY_TRIGGERS, {})
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
