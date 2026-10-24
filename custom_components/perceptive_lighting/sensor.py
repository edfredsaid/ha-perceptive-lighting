"""Sensor platform for Perceptive Lighting."""
from __future__ import annotations

from typing import Any

from homeassistant.components.sensor import (
    SensorDeviceClass,
    SensorEntity,
    SensorStateClass,
)
from homeassistant.config_entries import ConfigEntry
from homeassistant.const import PERCENTAGE
from homeassistant.core import HomeAssistant, callback
from homeassistant.helpers import area_registry as ar
from homeassistant.helpers.device_registry import DeviceInfo
from homeassistant.helpers.dispatcher import async_dispatcher_connect
from homeassistant.helpers.entity_platform import AddEntitiesCallback

from .const import (
    CCT_EXTERNAL,
    CONF_ADJUST_INTERVAL_MINUTES,
    CONF_CCT_MODE,
    CONF_DAYLIGHT_LEVEL,
    CONF_ON_OFF_COOLDOWN_MINUTES,
    CONF_TRANSITION_SECONDS,
    DAYLIGHT_MEDIUM,
    DEFAULT_ADJUST_INTERVAL_MINUTES,
    DEFAULT_ON_OFF_COOLDOWN_MINUTES,
    DEFAULT_TRANSITION_SECONDS,
    DOMAIN,
    NAME,
    STATE_BRIGHT,
    STATE_COMFORTABLE,
    STATE_DARK,
    STATE_DIM,
    VERSION,
)
from .coordinator import (
    PerceptiveLightingHub,
    RoomController,
    SIGNAL_HUB_UPDATED,
    SIGNAL_ROOM_UPDATED,
)


async def async_setup_entry(
    hass: HomeAssistant,
    entry: ConfigEntry,
    async_add_entities: AddEntitiesCallback,
) -> None:
    """Set up Perceptive Lighting sensors from a config entry."""
    hub: PerceptiveLightingHub = hass.data[DOMAIN][entry.entry_id]

    entities: list[SensorEntity] = [
        OutdoorEffectiveSkyLuxSensor(hub),
    ]

    for room in hub.rooms.values():
        entities.extend(
            [
                RoomPerceptiveIlluminanceSensor(room),
                RoomNaturalPerceptiveIlluminanceSensor(room),
                RoomLightingDeficitSensor(room),
                RoomPerceptiveStateSensor(room),
            ]
        )

    async_add_entities(entities)


class OutdoorEffectiveSkyLuxSensor(SensorEntity):
    """Global hub sensor reporting unified outdoor effective sky illuminance, time-of-day regime blend, and hub settings."""

    _attr_has_entity_name = True
    _attr_name = "Outdoor Effective Sky Illuminance"
    _attr_native_unit_of_measurement = "lx"
    _attr_device_class = SensorDeviceClass.ILLUMINANCE
    _attr_state_class = SensorStateClass.MEASUREMENT
    _attr_icon = "mdi:weather-partly-cloudy"

    def __init__(self, hub: PerceptiveLightingHub) -> None:
        """Initialize the outdoor sensor."""
        self.hub = hub
        self._attr_unique_id = f"{DOMAIN}_hub_outdoor_effective_lux"
        self._attr_device_info = DeviceInfo(
            identifiers={(DOMAIN, "hub")},
            name=f"{NAME} Hub",
            manufacturer="Perceptive Lighting",
            model="Outdoor Adaptation Engine",
            sw_version=VERSION,
        )

    async def async_added_to_hass(self) -> None:
        """Subscribe to hub updates."""
        self.async_on_remove(
            async_dispatcher_connect(
                self.hass, SIGNAL_HUB_UPDATED, self._handle_update
            )
        )

    @callback
    def _handle_update(self) -> None:
        self.async_write_ha_state()

    @property
    def native_value(self) -> float:
        """Return effective outdoor sky lux."""
        return self.hub.outdoor_state.effective_sky_lux

    @property
    def extra_state_attributes(self) -> dict[str, Any]:
        """Return rich outdoor adaptation attributes and hub configuration for Sidebar UI and Lovelace cards."""
        st = self.hub.outdoor_state
        area_reg = ar.async_get(self.hass)
        all_areas = [
            {"area_id": a.id, "name": a.name}
            for a in sorted(area_reg.async_list_areas(), key=lambda x: x.name.lower())
        ]
        return {
            "raw_outdoor_lux": st.raw_outdoor_lux,
            "is_synthetic_lux": st.is_synthetic_lux,
            "weather_condition": st.weather_condition,
            "cloud_cover_ratio": st.cloud_cover_ratio,
            "diffuse_sky_factor": st.diffuse_sky_factor,
            "solar_elevation": st.solar_elevation,
            "solar_azimuth": st.solar_azimuth,
            "is_daytime": st.is_daytime,
            "circadian_factor": st.circadian_factor,
            "sky_quality": st.sky_quality,
            "outdoor_temp_c": st.outdoor_temp_c,
            "day_length_hours": st.day_length_hours,
            "time_regime": st.time_regime,
            "regime_weights": st.regime_weights,
            "regime_explanation": st.regime_explanation,
            "regime_schedule": dict(self.hub.regime_schedule),
            "weather_entity": self.hub.weather_entity,
            "outdoor_sensors": list(self.hub.outdoor_sensors),
            "global_lock": self.hub.global_lock,
            "outdoor_sensor_profiles": {
                k: v.to_dict() for k, v in self.hub.outdoor_profiles.items()
            },
            "configured_rooms": list(self.hub.rooms.keys()),
            "all_ha_areas": all_areas,
        }


class BaseRoomSensor(SensorEntity):
    """Base class for per-room Perceptive Lighting sensors."""

    _attr_has_entity_name = True

    def __init__(self, room: RoomController, key: str, name: str) -> None:
        """Initialize room sensor."""
        self.room = room
        self._attr_name = name
        self._attr_unique_id = f"{DOMAIN}_{room.room_id}_{key}"
        self._attr_device_info = DeviceInfo(
            identifiers={(DOMAIN, f"room_{room.room_id}")},
            name=f"{room.room_name} Perceptive Lighting",
            manufacturer="Perceptive Lighting",
            model="Room Perceptive Illuminance Controller",
            sw_version=VERSION,
            suggested_area=room.area_id,
        )

    async def async_added_to_hass(self) -> None:
        """Subscribe to room updates."""
        self.async_on_remove(
            async_dispatcher_connect(
                self.hass,
                f"{SIGNAL_ROOM_UPDATED}_{self.room.room_id}",
                self._handle_update,
            )
        )

    @callback
    def _handle_update(self) -> None:
        self.async_write_ha_state()


class RoomPerceptiveIlluminanceSensor(BaseRoomSensor):
    """Sensor reporting 0-100% total Perceptive Illuminance of the room."""

    _attr_native_unit_of_measurement = PERCENTAGE
    _attr_state_class = SensorStateClass.MEASUREMENT
    _attr_icon = "mdi:brightness-percent"

    def __init__(self, room: RoomController) -> None:
        super().__init__(room, "perceptive_illuminance", "Perceptive Illuminance")

    @property
    def native_value(self) -> float:
        return self.room.result.perceptive_illuminance_pct

    @property
    def extra_state_attributes(self) -> dict[str, Any]:
        """Expose complete room diagnostic, live reason, shedding priority, regime scenes, triggers, and UI state."""
        res = self.room.result
        return {
            "room_id": self.room.room_id,
            "room_name": self.room.room_name,
            "area_id": self.room.area_id,
            "operating_mode": self.room.operating_mode,
            "daylight_level": self.room.config.get(CONF_DAYLIGHT_LEVEL, DAYLIGHT_MEDIUM),
            "cct_mode": self.room.config.get(CONF_CCT_MODE, CCT_EXTERNAL),
            "on_off_cooldown_minutes": float(
                self.room.config.get(
                    CONF_ON_OFF_COOLDOWN_MINUTES, DEFAULT_ON_OFF_COOLDOWN_MINUTES
                )
            ),
            "adjust_interval_minutes": float(
                self.room.config.get(
                    CONF_ADJUST_INTERVAL_MINUTES, DEFAULT_ADJUST_INTERVAL_MINUTES
                )
            ),
            "transition_seconds": float(
                self.room.config.get(
                    CONF_TRANSITION_SECONDS, DEFAULT_TRANSITION_SECONDS
                )
            ),
            "time_regime": res.time_regime,
            "regime_weights": res.regime_weights,
            "active_illum_model": res.active_illum_model,
            "active_weather_model": res.active_weather_model,
            "active_season_model": res.active_season_model,
            "weather_modifier": res.weather_modifier,
            "season_thermal_modifier": res.season_thermal_modifier,
            "indoor_temp_c": res.indoor_temp_c,
            "indoor_humidity_pct": res.indoor_humidity_pct,
            "raw_lux": res.raw_lux,
            "effective_spatial_lux": res.effective_spatial_lux,
            "natural_spatial_lux": res.natural_spatial_lux,
            "artificial_lux_contribution": res.artificial_lux_contribution,
            "min_artificial_lux": res.min_artificial_lux,
            "target_comfort_lux": res.target_comfort_lux,
            "turn_on_lux_threshold": res.turn_on_lux_threshold,
            "turn_off_lux_threshold": res.turn_off_lux_threshold,
            "natural_perceptive_illuminance_pct": res.natural_perceptive_illuminance_pct,
            "lighting_deficit_pct": res.lighting_deficit_pct,
            "perceptive_state": res.perceptive_state,
            "should_turn_lights_on": res.should_turn_lights_on,
            "should_turn_lights_off_while_occupied": res.should_turn_lights_off_while_occupied,
            "diffuse_modifier": res.diffuse_modifier,
            "direct_sun_in_window": res.direct_sun_in_window,
            "internal_contrast_ratio": res.internal_contrast_ratio,
            "outdoor_adaptation_offset_lux": res.outdoor_adaptation_offset_lux,
            "recommended_brightness_pct": res.recommended_brightness_pct,
            "recommended_shed_count": res.recommended_shed_count,
            "active_shed_count": self.room.active_shed_count,
            "shed_light_entities": self.room.shed_light_entities,
            "recommended_cct_kelvin": res.recommended_cct_kelvin,
            "active_activity": self.room.active_activity,
            "active_scene_id": self.room.active_scene_id,
            "resolved_scene_id": self.room.resolved_scene_id,
            "resolved_scene_reason": self.room.resolved_scene_reason,
            "decision_reason": self.room.decision_reason,
            "available_scenes": self.room.get_available_scene_options(),
            "regime_configs": self.room.regime_configs,
            "scene_triggers": self.room.scene_triggers,
            "light_priority_order": self.room.light_priority_order,
            "light_order_locked": self.room.light_order_locked,
            "light_metadata": self.room.light_metadata,
            "is_occupied": self.room.is_occupied,
            "occupied_by_entities": self.room.occupied_by_entities,
            "automation_locked": self.room.automation_locked,
            "manual_override_active": self.room.manual_override_active,
            "cooldown_active": self.room.cooldown_active,
            "feel_nudge_day_pct": round(self.room.feel_nudge_day_pct, 1),
            "feel_nudge_night_pct": round(self.room.feel_nudge_night_pct, 1),
            "window_azimuth": self.room.window_azimuth,
            "included_lux_sensors": self.room.lux_sensors,
            "included_temp_sensors": self.room.temp_sensors,
            "included_humidity_sensors": self.room.humidity_sensors,
            "included_presence_sensors": self.room.presence_sensors,
            "included_media_players": self.room.media_players,
            "included_lights": self.room.lights,
            "excluded_entities": list(self.room.excluded_entities),
            "auto_excluded_helpers": self.room.auto_excluded_helpers,
            "all_area_entities": self.room.all_area_entities,
            "sensor_breakdown": res.sensor_breakdown,
            "sensor_dynamic_weights": res.sensor_dynamic_weights,
            "sensor_profiles": {
                k: v.to_dict() for k, v in self.room.sensor_profiles.items()
            },
            "learned_weights": self.room.learned_weights.to_dict(),
            "ai_insight": self.room.learned_weights.last_ai_insight,
            "ai_last_optimized_utc": self.room.learned_weights.last_ai_optimized_utc,
            "ai_total_optimizations": self.room.learned_weights.total_optimizations,
            "observation_count": len(self.room.observations),
        }


class RoomNaturalPerceptiveIlluminanceSensor(BaseRoomSensor):
    """Sensor reporting 0-100% Natural Daylight Perceptive Illuminance (ignoring active room lights)."""

    _attr_native_unit_of_measurement = PERCENTAGE
    _attr_state_class = SensorStateClass.MEASUREMENT
    _attr_icon = "mdi:weather-sunny"

    def __init__(self, room: RoomController) -> None:
        super().__init__(
            room,
            "natural_perceptive_illuminance",
            "Natural Perceptive Illuminance",
        )

    @property
    def native_value(self) -> float:
        return self.room.result.natural_perceptive_illuminance_pct

    @property
    def extra_state_attributes(self) -> dict[str, Any]:
        res = self.room.result
        return {
            "natural_spatial_lux": res.natural_spatial_lux,
            "target_comfort_lux": res.target_comfort_lux,
            "turn_on_lux_threshold": res.turn_on_lux_threshold,
            "turn_off_lux_threshold": res.turn_off_lux_threshold,
            "outdoor_adaptation_offset_lux": res.outdoor_adaptation_offset_lux,
            "diffuse_modifier": res.diffuse_modifier,
            "direct_sun_in_window": res.direct_sun_in_window,
        }


class RoomLightingDeficitSensor(BaseRoomSensor):
    """Sensor reporting 0-100% Supplemental Lighting Need / Deficit."""

    _attr_native_unit_of_measurement = PERCENTAGE
    _attr_state_class = SensorStateClass.MEASUREMENT
    _attr_icon = "mdi:lightbulb-on-50"

    def __init__(self, room: RoomController) -> None:
        super().__init__(room, "lighting_deficit", "Lighting Deficit")

    @property
    def native_value(self) -> float:
        return self.room.result.lighting_deficit_pct

    @property
    def extra_state_attributes(self) -> dict[str, Any]:
        res = self.room.result
        return {
            "recommended_brightness_pct": res.recommended_brightness_pct,
            "recommended_shed_count": res.recommended_shed_count,
            "recommended_cct_kelvin": res.recommended_cct_kelvin,
            "active_activity": self.room.active_activity,
            "resolved_scene_id": self.room.resolved_scene_id,
        }


class RoomPerceptiveStateSensor(BaseRoomSensor):
    """Enum sensor reporting whether the room currently feels dark, dim, comfortable, or bright."""

    _attr_device_class = SensorDeviceClass.ENUM
    _attr_options = [STATE_DARK, STATE_DIM, STATE_COMFORTABLE, STATE_BRIGHT]
    _attr_icon = "mdi:eye-outline"

    def __init__(self, room: RoomController) -> None:
        super().__init__(room, "perceptive_state", "Perceptive State")

    @property
    def native_value(self) -> str:
        return self.room.result.perceptive_state
