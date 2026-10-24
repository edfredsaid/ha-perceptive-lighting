"""Config flow and Options flow for Perceptive Lighting."""
from __future__ import annotations

from typing import Any
import voluptuous as vol

from homeassistant import config_entries
from homeassistant.core import HomeAssistant, callback
from homeassistant.data_entry_flow import FlowResult
from homeassistant.helpers import (
    area_registry as ar,
    device_registry as dr,
    entity_registry as er,
    selector,
)

from .const import (
    ACTIVITY_DEFINITIONS,
    ACTIVITY_RELAXING,
    CCT_EXTERNAL,
    CCT_MODES,
    CONF_ACTIVITIES,
    CONF_AREA_ID,
    CONF_BLOCKER_ENTITIES,
    CONF_CCT_MODE,
    CONF_DAYLIGHT_LEVEL,
    CONF_DEFAULT_ACTIVITY,
    CONF_EXCLUDED_ENTITIES,
    CONF_INCLUDE_MEDIA_PLAYERS,
    CONF_LINKED_HA_SCENES,
    CONF_ON_OFF_COOLDOWN_MINUTES,
    CONF_OPERATING_MODE,
    CONF_OUTDOOR_SENSORS,
    CONF_ROOM_ID,
    CONF_ROOM_NAME,
    CONF_ROOMS,
    CONF_SENSOR_PROFILES,
    CONF_SUN_ENTITY,
    CONF_SUN_WINDOW_DIRECTION,
    CONF_WEATHER_ENTITY,
    DAYLIGHT_LEVELS,
    DAYLIGHT_MEDIUM,
    DEFAULT_ON_OFF_COOLDOWN_MINUTES,
    DOMAIN,
    MODE_FULL_ADAPTIVE,
    MODE_SENSOR_ONLY,
    OPERATING_MODES,
    PLACEMENT_AUTO,
    SENSOR_PLACEMENTS,
    SUN_DIR_AUTO,
    SUN_DIRECTIONS,
)


def _discover_area_candidates(
    hass: HomeAssistant, area_id: str
) -> dict[str, list[str]]:
    """Return all candidate illuminance, presence, media_player, light, and scene entities in an HA Area."""
    ent_reg = er.async_get(hass)
    dev_reg = dr.async_get(hass)
    area_devs = {
        d.id
        for d in dev_reg.devices.values()
        if d.area_id == area_id and not d.disabled_by
    }

    lux: list[str] = []
    pres: list[str] = []
    media: list[str] = []
    lights: list[str] = []
    scenes: list[str] = []

    for entry in ent_reg.entities.values():
        if entry.disabled_by or entry.platform == DOMAIN:
            continue
        in_area = entry.area_id == area_id or (
            entry.area_id is None and entry.device_id in area_devs
        )
        if not in_area:
            continue

        eid = entry.entity_id
        dom = entry.domain
        st = hass.states.get(eid)
        dclass = (
            entry.device_class
            or entry.original_device_class
            or (st.attributes.get("device_class") if st else None)
        )
        unit = entry.unit_of_measurement or (
            st.attributes.get("unit_of_measurement") if st else None
        )

        if dom == "sensor" and (dclass == "illuminance" or unit in ("lx", "lm/m²")):
            lux.append(eid)
        elif dom == "binary_sensor" and dclass in ("motion", "occupancy", "presence"):
            pres.append(eid)
        elif dom == "media_player":
            media.append(eid)
        elif dom == "light":
            lights.append(eid)
        elif dom == "scene":
            scenes.append(eid)

    return {
        "lux": sorted(lux),
        "presence": sorted(pres),
        "media": sorted(media),
        "lights": sorted(lights),
        "scenes": sorted(scenes),
        "all": sorted(lux + pres + media + lights),
    }


class PerceptiveLightingConfigFlow(config_entries.ConfigFlow, domain=DOMAIN):
    """Handle a config flow for Perceptive Lighting."""

    VERSION = 1

    def __init__(self) -> None:
        """Initialize the config flow."""
        self._hub_data: dict[str, Any] = {}
        self._rooms: dict[str, dict[str, Any]] = {}
        self._draft_room: dict[str, Any] = {}

    async def async_step_user(
        self, user_input: dict[str, Any] | None = None
    ) -> FlowResult:
        """Step 1: Global Setup (Weather Entity & Outdoor Illuminance Sensors)."""
        if self._async_current_entries():
            return self.async_abort(reason="single_instance_allowed")

        if user_input is not None:
            self._hub_data = {
                CONF_WEATHER_ENTITY: user_input.get(CONF_WEATHER_ENTITY),
                CONF_OUTDOOR_SENSORS: user_input.get(CONF_OUTDOOR_SENSORS, []),
                CONF_SUN_ENTITY: "sun.sun",
                CONF_ROOMS: {},
            }
            return await self.async_step_add_room_prompt()

        # Auto-detect default weather entity
        weather_states = self.hass.states.async_entity_ids("weather")
        default_weather = weather_states[0] if weather_states else None

        schema = vol.Schema(
            {
                vol.Optional(
                    CONF_WEATHER_ENTITY, default=default_weather
                ): selector.EntitySelector(
                    selector.EntitySelectorConfig(domain="weather")
                ),
                vol.Optional(
                    CONF_OUTDOOR_SENSORS, default=[]
                ): selector.EntitySelector(
                    selector.EntitySelectorConfig(
                        domain="sensor",
                        device_class="illuminance",
                        multiple=True,
                    )
                ),
            }
        )

        return self.async_show_form(
            step_id="user",
            data_schema=schema,
        )

    async def async_step_add_room_prompt(
        self, user_input: dict[str, Any] | None = None
    ) -> FlowResult:
        """Ask if the user wants to add a room now or finish setup."""
        if user_input is not None:
            if user_input.get("add_another_room"):
                return await self.async_step_room_basics()
            self._hub_data[CONF_ROOMS] = self._rooms
            return self.async_create_entry(
                title="Perceptive Lighting",
                data=self._hub_data,
            )

        return self.async_show_form(
            step_id="add_room_prompt",
            data_schema=vol.Schema(
                {
                    vol.Required("add_another_room", default=len(self._rooms) == 0): bool,
                }
            ),
            description_placeholders={"room_count": str(len(self._rooms))},
        )

    async def async_step_room_basics(
        self, user_input: dict[str, Any] | None = None
    ) -> FlowResult:
        """Room Wizard Step 1: Select HA Area, Operating Mode, Activities & Daylight."""
        if user_input is not None:
            area_id = user_input[CONF_AREA_ID]
            area_reg = ar.async_get(self.hass)
            area_entry = area_reg.async_get_area(area_id)
            room_name = area_entry.name if area_entry else area_id

            activities = user_input.get(CONF_ACTIVITIES) or [ACTIVITY_RELAXING]
            self._draft_room = {
                CONF_ROOM_ID: area_id,
                CONF_ROOM_NAME: room_name,
                CONF_AREA_ID: area_id,
                CONF_OPERATING_MODE: user_input[CONF_OPERATING_MODE],
                CONF_ACTIVITIES: activities,
                CONF_DEFAULT_ACTIVITY: activities[0],
                CONF_DAYLIGHT_LEVEL: user_input[CONF_DAYLIGHT_LEVEL],
                CONF_SUN_WINDOW_DIRECTION: user_input[CONF_SUN_WINDOW_DIRECTION],
            }
            return await self.async_step_room_entities()

        activity_options = [
            selector.SelectOptionDict(value=k, label=v["name"])
            for k, v in ACTIVITY_DEFINITIONS.items()
        ]
        mode_options = [
            selector.SelectOptionDict(value=k, label=v)
            for k, v in OPERATING_MODES.items()
        ]
        daylight_options = [
            selector.SelectOptionDict(value=k, label=v)
            for k, v in DAYLIGHT_LEVELS.items()
        ]
        sun_dir_options = [
            selector.SelectOptionDict(value=k, label=v)
            for k, v in SUN_DIRECTIONS.items()
        ]

        schema = vol.Schema(
            {
                vol.Required(CONF_AREA_ID): selector.AreaSelector(),
                vol.Required(
                    CONF_OPERATING_MODE, default=MODE_FULL_ADAPTIVE
                ): selector.SelectSelector(
                    selector.SelectSelectorConfig(
                        options=mode_options,
                        mode=selector.SelectSelectorMode.DROPDOWN,
                    )
                ),
                vol.Required(
                    CONF_ACTIVITIES,
                    default=[ACTIVITY_RELAXING, "working", "reading", "film"],
                ): selector.SelectSelector(
                    selector.SelectSelectorConfig(
                        options=activity_options,
                        multiple=True,
                        mode=selector.SelectSelectorMode.LIST,
                    )
                ),
                vol.Required(
                    CONF_DAYLIGHT_LEVEL, default=DAYLIGHT_MEDIUM
                ): selector.SelectSelector(
                    selector.SelectSelectorConfig(
                        options=daylight_options,
                        mode=selector.SelectSelectorMode.DROPDOWN,
                    )
                ),
                vol.Required(
                    CONF_SUN_WINDOW_DIRECTION, default=SUN_DIR_AUTO
                ): selector.SelectSelector(
                    selector.SelectSelectorConfig(
                        options=sun_dir_options,
                        mode=selector.SelectSelectorMode.DROPDOWN,
                    )
                ),
            }
        )

        return self.async_show_form(step_id="room_basics", data_schema=schema)

    async def async_step_room_entities(
        self, user_input: dict[str, Any] | None = None
    ) -> FlowResult:
        """Room Wizard Step 2: Area Exclusion Blacklist, Per-Sensor Placement, and Controller Settings."""
        area_id = self._draft_room[CONF_AREA_ID]
        candidates = _discover_area_candidates(self.hass, area_id)

        if user_input is not None:
            excluded = user_input.get(CONF_EXCLUDED_ENTITIES, [])
            sensor_profiles: dict[str, Any] = {}
            for lux_eid in candidates["lux"]:
                if lux_eid not in excluded:
                    placement_key = f"placement_{lux_eid}"
                    placement_val = user_input.get(placement_key, PLACEMENT_AUTO)
                    sensor_profiles[lux_eid] = {
                        "entity_id": lux_eid,
                        "placement": placement_val,
                    }

            self._draft_room[CONF_EXCLUDED_ENTITIES] = excluded
            self._draft_room[CONF_SENSOR_PROFILES] = sensor_profiles
            self._draft_room[CONF_INCLUDE_MEDIA_PLAYERS] = user_input.get(
                CONF_INCLUDE_MEDIA_PLAYERS, True
            )
            self._draft_room[CONF_CCT_MODE] = user_input.get(
                CONF_CCT_MODE, CCT_EXTERNAL
            )
            self._draft_room[CONF_LINKED_HA_SCENES] = user_input.get(
                CONF_LINKED_HA_SCENES, candidates["scenes"]
            )
            self._draft_room[CONF_ON_OFF_COOLDOWN_MINUTES] = user_input.get(
                CONF_ON_OFF_COOLDOWN_MINUTES, DEFAULT_ON_OFF_COOLDOWN_MINUTES
            )

            rid = self._draft_room[CONF_ROOM_ID]
            self._rooms[rid] = dict(self._draft_room)
            return await self.async_step_add_room_prompt()

        fields: dict[Any, Any] = {}

        # 1. Exclusion Blacklist (all discovered entities in the room are included by default)
        if candidates["all"]:
            fields[
                vol.Optional(CONF_EXCLUDED_ENTITIES, default=[])
            ] = selector.EntitySelector(
                selector.EntitySelectorConfig(
                    include_entities=candidates["all"],
                    multiple=True,
                )
            )

        # 2. Per-Sensor Placement Calibration for each discovered illuminance sensor in the room
        placement_options = [
            selector.SelectOptionDict(value=k, label=v)
            for k, v in SENSOR_PLACEMENTS.items()
        ]
        for lux_eid in candidates["lux"]:
            fields[
                vol.Optional(f"placement_{lux_eid}", default=PLACEMENT_AUTO)
            ] = selector.SelectSelector(
                selector.SelectSelectorConfig(
                    options=placement_options,
                    mode=selector.SelectSelectorMode.DROPDOWN,
                )
            )

        # 3. Controller options if not Sensor-Only
        if self._draft_room[CONF_OPERATING_MODE] != MODE_SENSOR_ONLY:
            fields[
                vol.Optional(CONF_INCLUDE_MEDIA_PLAYERS, default=True)
            ] = bool

            cct_options = [
                selector.SelectOptionDict(value=k, label=v)
                for k, v in CCT_MODES.items()
            ]
            fields[
                vol.Optional(CONF_CCT_MODE, default=CCT_EXTERNAL)
            ] = selector.SelectSelector(
                selector.SelectSelectorConfig(
                    options=cct_options,
                    mode=selector.SelectSelectorMode.DROPDOWN,
                )
            )

            fields[
                vol.Optional(
                    CONF_LINKED_HA_SCENES, default=candidates["scenes"]
                )
            ] = selector.EntitySelector(
                selector.EntitySelectorConfig(
                    domain="scene",
                    multiple=True,
                )
            )

            fields[
                vol.Optional(
                    CONF_ON_OFF_COOLDOWN_MINUTES,
                    default=DEFAULT_ON_OFF_COOLDOWN_MINUTES,
                )
            ] = selector.NumberSelector(
                selector.NumberSelectorConfig(
                    min=1,
                    max=30,
                    step=1,
                    unit_of_measurement="min",
                    mode=selector.NumberSelectorMode.BOX,
                )
            )

        return self.async_show_form(
            step_id="room_entities",
            data_schema=vol.Schema(fields),
            description_placeholders={
                "room_name": self._draft_room[CONF_ROOM_NAME],
                "lux_count": str(len(candidates["lux"])),
                "light_count": str(len(candidates["lights"])),
                "presence_count": str(len(candidates["presence"])),
            },
        )

    @staticmethod
    @callback
    def async_get_options_flow(
        config_entry: config_entries.ConfigEntry,
    ) -> config_entries.OptionsFlow:
        """Get the options flow for adding/editing rooms or global settings."""
        return PerceptiveLightingOptionsFlow(config_entry)


class PerceptiveLightingOptionsFlow(config_entries.OptionsFlow):
    """Handle options flow for adding/editing rooms and global sensors."""

    def __init__(self, config_entry: config_entries.ConfigEntry) -> None:
        """Initialize options flow."""
        self._entry = config_entry
        self._rooms: dict[str, dict[str, Any]] = dict(
            config_entry.options.get(
                CONF_ROOMS, config_entry.data.get(CONF_ROOMS, {})
            )
        )
        self._draft_room: dict[str, Any] = {}

    async def async_step_init(
        self, user_input: dict[str, Any] | None = None
    ) -> FlowResult:
        """Options Menu: Add/Edit Room, Remove Room, or Update Global Outdoor Settings."""
        if user_input is not None:
            action = user_input.get("action")
            if action == "add_edit_room":
                return await self.async_step_room_basics()
            if action == "remove_room":
                return await self.async_step_remove_room()
            if action == "global_settings":
                return await self.async_step_global_settings()

        actions = [
            selector.SelectOptionDict(
                value="add_edit_room", label="Add or Configure a Room"
            ),
            selector.SelectOptionDict(
                value="global_settings",
                label="Global Outdoor Sensors & Weather Settings",
            ),
        ]
        if self._rooms:
            actions.append(
                selector.SelectOptionDict(
                    value="remove_room", label="Remove a Room"
                )
            )

        return self.async_show_form(
            step_id="init",
            data_schema=vol.Schema(
                {
                    vol.Required("action", default="add_edit_room"): selector.SelectSelector(
                        selector.SelectSelectorConfig(
                            options=actions,
                            mode=selector.SelectSelectorMode.LIST,
                        )
                    )
                }
            ),
        )

    async def async_step_global_settings(
        self, user_input: dict[str, Any] | None = None
    ) -> FlowResult:
        """Edit global weather entity and outdoor illuminance sensors."""
        if user_input is not None:
            new_options = dict(self._entry.options or self._entry.data)
            new_options[CONF_WEATHER_ENTITY] = user_input.get(CONF_WEATHER_ENTITY)
            new_options[CONF_OUTDOOR_SENSORS] = user_input.get(
                CONF_OUTDOOR_SENSORS, []
            )
            new_options[CONF_ROOMS] = self._rooms
            return self.async_create_entry(title="", data=new_options)

        cur_weather = self._entry.options.get(
            CONF_WEATHER_ENTITY, self._entry.data.get(CONF_WEATHER_ENTITY)
        )
        cur_outdoor = self._entry.options.get(
            CONF_OUTDOOR_SENSORS,
            self._entry.data.get(CONF_OUTDOOR_SENSORS, []),
        )

        return self.async_show_form(
            step_id="global_settings",
            data_schema=vol.Schema(
                {
                    vol.Optional(
                        CONF_WEATHER_ENTITY, default=cur_weather
                    ): selector.EntitySelector(
                        selector.EntitySelectorConfig(domain="weather")
                    ),
                    vol.Optional(
                        CONF_OUTDOOR_SENSORS, default=cur_outdoor
                    ): selector.EntitySelector(
                        selector.EntitySelectorConfig(
                            domain="sensor",
                            device_class="illuminance",
                            multiple=True,
                        )
                    ),
                }
            ),
        )

    async def async_step_room_basics(
        self, user_input: dict[str, Any] | None = None
    ) -> FlowResult:
        """Add or edit a room Step 1."""
        if user_input is not None:
            area_id = user_input[CONF_AREA_ID]
            area_reg = ar.async_get(self.hass)
            area_entry = area_reg.async_get_area(area_id)
            room_name = area_entry.name if area_entry else area_id

            existing = self._rooms.get(area_id, {})
            activities = user_input.get(CONF_ACTIVITIES) or [ACTIVITY_RELAXING]
            self._draft_room = {
                **existing,
                CONF_ROOM_ID: area_id,
                CONF_ROOM_NAME: room_name,
                CONF_AREA_ID: area_id,
                CONF_OPERATING_MODE: user_input[CONF_OPERATING_MODE],
                CONF_ACTIVITIES: activities,
                CONF_DEFAULT_ACTIVITY: activities[0],
                CONF_DAYLIGHT_LEVEL: user_input[CONF_DAYLIGHT_LEVEL],
                CONF_SUN_WINDOW_DIRECTION: user_input[CONF_SUN_WINDOW_DIRECTION],
            }
            return await self.async_step_room_entities()

        activity_options = [
            selector.SelectOptionDict(value=k, label=v["name"])
            for k, v in ACTIVITY_DEFINITIONS.items()
        ]
        mode_options = [
            selector.SelectOptionDict(value=k, label=v)
            for k, v in OPERATING_MODES.items()
        ]
        daylight_options = [
            selector.SelectOptionDict(value=k, label=v)
            for k, v in DAYLIGHT_LEVELS.items()
        ]
        sun_dir_options = [
            selector.SelectOptionDict(value=k, label=v)
            for k, v in SUN_DIRECTIONS.items()
        ]

        return self.async_show_form(
            step_id="room_basics",
            data_schema=vol.Schema(
                {
                    vol.Required(CONF_AREA_ID): selector.AreaSelector(),
                    vol.Required(
                        CONF_OPERATING_MODE, default=MODE_FULL_ADAPTIVE
                    ): selector.SelectSelector(
                        selector.SelectSelectorConfig(
                            options=mode_options,
                            mode=selector.SelectSelectorMode.DROPDOWN,
                        )
                    ),
                    vol.Required(
                        CONF_ACTIVITIES,
                        default=[
                            ACTIVITY_RELAXING,
                            "working",
                            "reading",
                            "film",
                        ],
                    ): selector.SelectSelector(
                        selector.SelectSelectorConfig(
                            options=activity_options,
                            multiple=True,
                            mode=selector.SelectSelectorMode.LIST,
                        )
                    ),
                    vol.Required(
                        CONF_DAYLIGHT_LEVEL, default=DAYLIGHT_MEDIUM
                    ): selector.SelectSelector(
                        selector.SelectSelectorConfig(
                            options=daylight_options,
                            mode=selector.SelectSelectorMode.DROPDOWN,
                        )
                    ),
                    vol.Required(
                        CONF_SUN_WINDOW_DIRECTION, default=SUN_DIR_AUTO
                    ): selector.SelectSelector(
                        selector.SelectSelectorConfig(
                            options=sun_dir_options,
                            mode=selector.SelectSelectorMode.DROPDOWN,
                        )
                    ),
                }
            ),
        )

    async def async_step_room_entities(
        self, user_input: dict[str, Any] | None = None
    ) -> FlowResult:
        """Add or edit room Step 2."""
        area_id = self._draft_room[CONF_AREA_ID]
        candidates = _discover_area_candidates(self.hass, area_id)

        if user_input is not None:
            excluded = user_input.get(CONF_EXCLUDED_ENTITIES, [])
            sensor_profiles: dict[str, Any] = {}
            for lux_eid in candidates["lux"]:
                if lux_eid not in excluded:
                    placement_val = user_input.get(
                        f"placement_{lux_eid}", PLACEMENT_AUTO
                    )
                    sensor_profiles[lux_eid] = {
                        "entity_id": lux_eid,
                        "placement": placement_val,
                    }

            self._draft_room[CONF_EXCLUDED_ENTITIES] = excluded
            self._draft_room[CONF_SENSOR_PROFILES] = sensor_profiles
            self._draft_room[CONF_INCLUDE_MEDIA_PLAYERS] = user_input.get(
                CONF_INCLUDE_MEDIA_PLAYERS, True
            )
            self._draft_room[CONF_CCT_MODE] = user_input.get(
                CONF_CCT_MODE, CCT_EXTERNAL
            )
            self._draft_room[CONF_LINKED_HA_SCENES] = user_input.get(
                CONF_LINKED_HA_SCENES, candidates["scenes"]
            )
            self._draft_room[CONF_ON_OFF_COOLDOWN_MINUTES] = user_input.get(
                CONF_ON_OFF_COOLDOWN_MINUTES, DEFAULT_ON_OFF_COOLDOWN_MINUTES
            )

            self._rooms[area_id] = dict(self._draft_room)
            new_options = dict(self._entry.options or self._entry.data)
            new_options[CONF_ROOMS] = self._rooms
            return self.async_create_entry(title="", data=new_options)

        fields: dict[Any, Any] = {}
        cur_excluded = self._draft_room.get(CONF_EXCLUDED_ENTITIES, [])
        if candidates["all"]:
            fields[
                vol.Optional(CONF_EXCLUDED_ENTITIES, default=cur_excluded)
            ] = selector.EntitySelector(
                selector.EntitySelectorConfig(
                    include_entities=candidates["all"],
                    multiple=True,
                )
            )

        placement_options = [
            selector.SelectOptionDict(value=k, label=v)
            for k, v in SENSOR_PLACEMENTS.items()
        ]
        existing_profiles = self._draft_room.get(CONF_SENSOR_PROFILES, {})
        for lux_eid in candidates["lux"]:
            cur_prof = existing_profiles.get(lux_eid, {})
            cur_place = (
                cur_prof.get("placement", PLACEMENT_AUTO)
                if isinstance(cur_prof, dict)
                else PLACEMENT_AUTO
            )
            fields[
                vol.Optional(f"placement_{lux_eid}", default=cur_place)
            ] = selector.SelectSelector(
                selector.SelectSelectorConfig(
                    options=placement_options,
                    mode=selector.SelectSelectorMode.DROPDOWN,
                )
            )

        if self._draft_room[CONF_OPERATING_MODE] != MODE_SENSOR_ONLY:
            fields[
                vol.Optional(
                    CONF_INCLUDE_MEDIA_PLAYERS,
                    default=self._draft_room.get(
                        CONF_INCLUDE_MEDIA_PLAYERS, True
                    ),
                )
            ] = bool

            cct_options = [
                selector.SelectOptionDict(value=k, label=v)
                for k, v in CCT_MODES.items()
            ]
            fields[
                vol.Optional(
                    CONF_CCT_MODE,
                    default=self._draft_room.get(CONF_CCT_MODE, CCT_EXTERNAL),
                )
            ] = selector.SelectSelector(
                selector.SelectSelectorConfig(
                    options=cct_options,
                    mode=selector.SelectSelectorMode.DROPDOWN,
                )
            )

            fields[
                vol.Optional(
                    CONF_LINKED_HA_SCENES,
                    default=self._draft_room.get(
                        CONF_LINKED_HA_SCENES, candidates["scenes"]
                    ),
                )
            ] = selector.EntitySelector(
                selector.EntitySelectorConfig(
                    domain="scene",
                    multiple=True,
                )
            )

            fields[
                vol.Optional(
                    CONF_ON_OFF_COOLDOWN_MINUTES,
                    default=self._draft_room.get(
                        CONF_ON_OFF_COOLDOWN_MINUTES,
                        DEFAULT_ON_OFF_COOLDOWN_MINUTES,
                    ),
                )
            ] = selector.NumberSelector(
                selector.NumberSelectorConfig(
                    min=1,
                    max=30,
                    step=1,
                    unit_of_measurement="min",
                    mode=selector.NumberSelectorMode.BOX,
                )
            )

        return self.async_show_form(
            step_id="room_entities",
            data_schema=vol.Schema(fields),
            description_placeholders={
                "room_name": self._draft_room[CONF_ROOM_NAME],
                "lux_count": str(len(candidates["lux"])),
                "light_count": str(len(candidates["lights"])),
                "presence_count": str(len(candidates["presence"])),
            },
        )

    async def async_step_remove_room(
        self, user_input: dict[str, Any] | None = None
    ) -> FlowResult:
        """Remove a configured room."""
        if user_input is not None:
            rid = user_input.get("room_to_remove")
            if rid in self._rooms:
                del self._rooms[rid]
            new_options = dict(self._entry.options or self._entry.data)
            new_options[CONF_ROOMS] = self._rooms
            return self.async_create_entry(title="", data=new_options)

        opts = [
            selector.SelectOptionDict(
                value=rid, label=rcfg.get(CONF_ROOM_NAME, rid)
            )
            for rid, rcfg in self._rooms.items()
        ]
        return self.async_show_form(
            step_id="remove_room",
            data_schema=vol.Schema(
                {
                    vol.Required("room_to_remove"): selector.SelectSelector(
                        selector.SelectSelectorConfig(
                            options=opts,
                            mode=selector.SelectSelectorMode.DROPDOWN,
                        )
                    )
                }
            ),
        )
