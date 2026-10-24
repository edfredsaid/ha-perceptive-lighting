"""Button platform for Perceptive Lighting (Micro-Step Feel Nudging & Calibration)."""
from __future__ import annotations

from homeassistant.components.button import ButtonEntity
from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant
from homeassistant.helpers.device_registry import DeviceInfo
from homeassistant.helpers.entity_platform import AddEntitiesCallback

from .const import DOMAIN, VERSION
from .coordinator import PerceptiveLightingHub, RoomController


async def async_setup_entry(
    hass: HomeAssistant,
    entry: ConfigEntry,
    async_add_entities: AddEntitiesCallback,
) -> None:
    """Set up Perceptive Lighting micro-nudge and calibration buttons."""
    hub: PerceptiveLightingHub = hass.data[DOMAIN][entry.entry_id]
    entities: list[ButtonEntity] = []
    for room in hub.rooms.values():
        entities.extend(
            [
                RoomNudgeNeedsMoreLightButton(room),
                RoomNudgeTooBrightButton(room),
                RoomCalibrateFeelsDarkNowButton(room),
            ]
        )
    async_add_entities(entities)


class BaseRoomButton(ButtonEntity):
    """Base button entity for a room."""

    _attr_has_entity_name = True

    def __init__(self, room: RoomController, key: str, name: str, icon: str) -> None:
        self.room = room
        self._attr_name = name
        self._attr_icon = icon
        self._attr_unique_id = f"{DOMAIN}_{room.room_id}_{key}"
        self._attr_device_info = DeviceInfo(
            identifiers={(DOMAIN, f"room_{room.room_id}")},
            name=f"{room.room_name} Perceptive Lighting",
            manufacturer="Perceptive Lighting",
            model="Room Perceptive Illuminance Controller",
            sw_version=VERSION,
            suggested_area=room.area_id,
        )


class RoomNudgeNeedsMoreLightButton(BaseRoomButton):
    """Micro-step button: Nudge room to feel like it needs slightly more light (+2%)."""

    def __init__(self, room: RoomController) -> None:
        super().__init__(
            room,
            "nudge_needs_more_light",
            "Feel: Needs More Light (+)",
            "mdi:brightness-5",
        )

    async def async_press(self) -> None:
        await self.room.async_nudge_feel("more_light")


class RoomNudgeTooBrightButton(BaseRoomButton):
    """Micro-step button: Nudge room to feel like it is slightly too bright (-2%)."""

    def __init__(self, room: RoomController) -> None:
        super().__init__(
            room,
            "nudge_too_bright",
            "Feel: Too Bright (-)",
            "mdi:brightness-4",
        )

    async def async_press(self) -> None:
        await self.room.async_nudge_feel("too_bright")


class RoomCalibrateFeelsDarkNowButton(BaseRoomButton):
    """One-tap teaching button: Calibrate room so current ambient level registers as dark."""

    def __init__(self, room: RoomController) -> None:
        super().__init__(
            room,
            "calibrate_feels_dark_now",
            "Calibrate: Feels Dark Now",
            "mdi:auto-fix",
        )

    async def async_press(self) -> None:
        await self.room.async_calibrate_feels_dark_now()
