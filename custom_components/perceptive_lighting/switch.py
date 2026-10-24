"""Switch platform for Perceptive Lighting (Automation Lock)."""
from __future__ import annotations

from typing import Any

from homeassistant.components.switch import SwitchEntity
from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant, callback
from homeassistant.helpers.device_registry import DeviceInfo
from homeassistant.helpers.dispatcher import (
    async_dispatcher_connect,
    async_dispatcher_send,
)
from homeassistant.helpers.entity_platform import AddEntitiesCallback

from .const import DOMAIN, NAME, VERSION
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
    """Set up Perceptive Lighting switch entities."""
    hub: PerceptiveLightingHub = hass.data[DOMAIN][entry.entry_id]
    entities: list[SwitchEntity] = [GlobalAutomationLockSwitch(hub)]
    for room in hub.rooms.values():
        entities.append(RoomAutomationLockSwitch(room))
    async_add_entities(entities)


class GlobalAutomationLockSwitch(SwitchEntity):
    """Switch to lock/pause all Perceptive Lighting room automations across the home."""

    _attr_has_entity_name = True
    _attr_name = "Global Automation Lock"
    _attr_icon = "mdi:home-lock"

    def __init__(self, hub: PerceptiveLightingHub) -> None:
        self.hub = hub
        self._attr_unique_id = f"{DOMAIN}_hub_global_automation_lock"
        self._attr_device_info = DeviceInfo(
            identifiers={(DOMAIN, "hub")},
            name=f"{NAME} Hub",
            manufacturer="Perceptive Lighting",
            model="Outdoor Adaptation Engine",
            sw_version=VERSION,
        )

    async def async_added_to_hass(self) -> None:
        self.async_on_remove(
            async_dispatcher_connect(
                self.hass, SIGNAL_HUB_UPDATED, self._handle_update
            )
        )

    @callback
    def _handle_update(self) -> None:
        self.async_write_ha_state()

    @property
    def is_on(self) -> bool:
        return self.hub.global_lock

    async def async_turn_on(self, **kwargs: Any) -> None:
        self.hub.global_lock = True
        await self.hub.async_save_runtime_state()
        async_dispatcher_send(self.hass, SIGNAL_HUB_UPDATED)

    async def async_turn_off(self, **kwargs: Any) -> None:
        self.hub.global_lock = False
        await self.hub.async_save_runtime_state()
        self.hub.async_update_outdoor_and_rooms(trigger_control=True)


class RoomAutomationLockSwitch(SwitchEntity):
    """Hard lock switch disabling all presence and lighting automation for a specific room."""

    _attr_has_entity_name = True
    _attr_name = "Automation Lock"

    def __init__(self, room: RoomController) -> None:
        self.room = room
        self._attr_unique_id = f"{DOMAIN}_{room.room_id}_automation_lock"
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

    @property
    def icon(self) -> str:
        return "mdi:lock" if self.room.automation_locked else "mdi:lock-open-variant"

    @property
    def is_on(self) -> bool:
        return self.room.automation_locked

    async def async_turn_on(self, **kwargs: Any) -> None:
        await self.room.async_set_automation_lock(True)

    async def async_turn_off(self, **kwargs: Any) -> None:
        await self.room.async_set_automation_lock(False)
