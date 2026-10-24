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
from homeassistant.helpers.device_registry import DeviceInfo
from homeassistant.helpers.dispatcher import async_dispatcher_connect
from homeassistant.helpers.entity_platform import AddEntitiesCallback

from .const import (
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
    """Global hub sensor reporting unified outdoor effective sky illuminance & adaptation data."""

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
        """Return rich outdoor adaptation attributes for diagnostics and Lovelace cards."""
        st = self.hub.outdoor_state
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
            "outdoor_sensor_profiles": {
                k: v.to_dict() for k, v in self.hub.outdoor_profiles.items()
            },
            "configured_rooms": list(self.hub.rooms.keys()),
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
        """Expose complete room diagnostic & UI card state attributes."""
        res = self.room.result
        return {
            "room_id": self.room.room_id,
            "room_name": self.room.room_name,
            "area_id": self.room.area_id,
            "operating_mode": self.room.operating_mode,
            "raw_lux": res.raw_lux,
            "effective_spatial_lux": res.effective_spatial_lux,
            "natural_spatial_lux": res.natural_spatial_lux,
            "artificial_lux_contribution": res.artificial_lux_contribution,
            "target_comfort_lux": res.target_comfort_lux,
            "natural_perceptive_illuminance_pct": res.natural_perceptive_illuminance_pct,
            "lighting_deficit_pct": res.lighting_deficit_pct,
            "perceptive_state": res.perceptive_state,
            "diffuse_modifier": res.diffuse_modifier,
            "direct_sun_in_window": res.direct_sun_in_window,
            "internal_contrast_ratio": res.internal_contrast_ratio,
            "outdoor_adaptation_offset_lux": res.outdoor_adaptation_offset_lux,
            "recommended_brightness_pct": res.recommended_brightness_pct,
            "recommended_cct_kelvin": res.recommended_cct_kelvin,
            "active_activity": self.room.active_activity,
            "active_scene_id": self.room.active_scene_id,
            "available_scenes": self.room.get_available_scene_options(),
            "is_occupied": self.room.is_occupied,
            "automation_locked": self.room.automation_locked,
            "manual_override_active": self.room.manual_override_active,
            "cooldown_active": self.room.cooldown_active,
            "feel_nudge_day_pct": round(self.room.feel_nudge_day_pct, 1),
            "feel_nudge_night_pct": round(self.room.feel_nudge_night_pct, 1),
            "window_azimuth": self.room.window_azimuth,
            "included_lux_sensors": self.room.lux_sensors,
            "included_presence_sensors": self.room.presence_sensors,
            "included_media_players": self.room.media_players,
            "included_lights": self.room.lights,
            "excluded_entities": list(self.room.excluded_entities),
            "all_area_entities": self.room.all_area_entities,
            "sensor_breakdown": res.sensor_breakdown,
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
            "recommended_cct_kelvin": res.recommended_cct_kelvin,
            "active_activity": self.room.active_activity,
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
