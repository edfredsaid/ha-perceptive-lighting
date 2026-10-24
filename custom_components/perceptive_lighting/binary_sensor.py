"""Binary Sensor platform for Perceptive Lighting (Manual Override & Cooldown status)."""
from __future__ import annotations

from homeassistant.components.binary_sensor import BinarySensorEntity
from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant, callback
from homeassistant.helpers.device_registry import DeviceInfo
from homeassistant.helpers.dispatcher import async_dispatcher_connect
from homeassistant.helpers.entity_platform import AddEntitiesCallback

from .const import DOMAIN, VERSION
from .coordinator import (
    PerceptiveLightingHub,
    RoomController,
    SIGNAL_ROOM_UPDATED,
)


async def async_setup_entry(
    hass: HomeAssistant,
    entry: ConfigEntry,
    async_add_entities: AddEntitiesCallback,
) -> None:
    """Set up Perceptive Lighting binary sensors."""
    hub: PerceptiveLightingHub = hass.data[DOMAIN][entry.entry_id]
    entities: list[BinarySensorEntity] = []
    for room in hub.rooms.values():
        entities.extend(
            [
                RoomManualOverrideBinarySensor(room),
                RoomCooldownActiveBinarySensor(room),
            ]
        )
    async_add_entities(entities)


class BaseRoomBinarySensor(BinarySensorEntity):
    """Base binary sensor for a room."""

    _attr_has_entity_name = True

    def __init__(self, room: RoomController, key: str, name: str) -> None:
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


class RoomManualOverrideBinarySensor(BaseRoomBinarySensor):
    """Indicates whether soft Manual Override is holding the room's lights."""

    _attr_icon = "mdi:hand-back-right-outline"

    def __init__(self, room: RoomController) -> None:
        super().__init__(room, "manual_override", "Manual Override Active")

    @property
    def is_on(self) -> bool:
        return self.room.manual_override_active


class RoomCooldownActiveBinarySensor(BaseRoomBinarySensor):
    """Indicates whether the 10-minute on/off anti-flicker lockout is active."""

    _attr_icon = "mdi:timer-sand"

    def __init__(self, room: RoomController) -> None:
        super().__init__(room, "cooldown_active", "Anti-Flicker Cooldown Active")

    @property
    def is_on(self) -> bool:
        return self.room.cooldown_active
