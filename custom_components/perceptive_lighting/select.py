"""Select platform for Perceptive Lighting (Adaptive Scene & Activity selector)."""
from __future__ import annotations

from homeassistant.components.select import SelectEntity
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
    """Set up Perceptive Lighting select entities."""
    hub: PerceptiveLightingHub = hass.data[DOMAIN][entry.entry_id]
    entities = [RoomAdaptiveSceneSelect(room) for room in hub.rooms.values()]
    async_add_entities(entities)


class RoomAdaptiveSceneSelect(SelectEntity):
    """Select entity to switch a room's active Adaptive Scene or Activity."""

    _attr_has_entity_name = True
    _attr_name = "Adaptive Scene"
    _attr_icon = "mdi:palette-swatch-variant"

    def __init__(self, room: RoomController) -> None:
        """Initialize the select entity."""
        self.room = room
        self._attr_unique_id = f"{DOMAIN}_{room.room_id}_adaptive_scene"
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

    @property
    def options(self) -> list[str]:
        """Return list of human-readable scene/activity names."""
        return list(self.room.get_available_scene_options().values())

    @property
    def current_option(self) -> str | None:
        """Return the currently active option label."""
        opts_map = self.room.get_available_scene_options()
        if self.room.active_scene_id in opts_map:
            return opts_map[self.room.active_scene_id]
        act_key = f"activity_{self.room.active_activity}"
        if act_key in opts_map:
            return opts_map[act_key]
        return next(iter(opts_map.values()), None)

    async def async_select_option(self, option: str) -> None:
        """Select a new Adaptive Scene or Activity by label or key."""
        opts_map = self.room.get_available_scene_options()
        for key, label in opts_map.items():
            if label == option or key == option:
                await self.room.async_select_scene_or_activity(key)
                return
        await self.room.async_select_scene_or_activity(option)
