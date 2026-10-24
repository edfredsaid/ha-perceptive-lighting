"""The Perceptive Lighting integration for Home Assistant."""
from __future__ import annotations

import logging
from pathlib import Path
from typing import Any
import voluptuous as vol

from homeassistant.components import frontend
from homeassistant.components.frontend import add_extra_js_url
from homeassistant.components.http import StaticPathConfig
from homeassistant.config_entries import ConfigEntry
from homeassistant.const import Platform
from homeassistant.core import HomeAssistant, ServiceCall

from .const import (
    CARD_JS_FILENAME,
    CONF_EXCLUDED_ENTITIES,
    CONF_ROOMS,
    DOMAIN,
    SERVICE_APPLY_ADAPTIVE_SCENE,
    SERVICE_CALIBRATE_FEELS_BRIGHT_NOW,
    SERVICE_CALIBRATE_FEELS_DARK_NOW,
    SERVICE_CAPTURE_ADAPTIVE_SCENE,
    SERVICE_DELETE_ADAPTIVE_SCENE,
    SERVICE_LOCK_ROOM,
    SERVICE_NUDGE_FEEL,
    SERVICE_OPTIMIZE_WITH_AI,
    SERVICE_RELEARN_HISTORY,
    SERVICE_RESET_ROOM,
    SERVICE_SET_WINDOW_COMPASS,
    SERVICE_UNLOCK_ROOM,
    SERVICE_UPDATE_HUB_CONFIG,
    SERVICE_UPDATE_ROOM_CONFIG,
    SERVICE_UPDATE_ROOM_EXCLUSIONS,
    SIDEBAR_PANEL_COMPONENT_NAME,
    SIDEBAR_PANEL_ICON,
    SIDEBAR_PANEL_TITLE,
    SIDEBAR_PANEL_URL_PATH,
    STATIC_URL_PATH,
    VERSION,
)
from .coordinator import PerceptiveLightingHub, RoomController

_LOGGER = logging.getLogger(__name__)

PLATFORMS: list[Platform] = [
    Platform.SENSOR,
    Platform.SELECT,
    Platform.SWITCH,
    Platform.BINARY_SENSOR,
    Platform.BUTTON,
]


async def async_setup(hass: HomeAssistant, config: dict[str, Any]) -> bool:
    """Set up the Perceptive Lighting component, Sidebar panel, and bundled Lovelace cards."""
    hass.data.setdefault(DOMAIN, {})
    await _async_register_frontend_cards_and_panel(hass)
    _async_register_services(hass)
    return True


async def async_setup_entry(hass: HomeAssistant, entry: ConfigEntry) -> bool:
    """Set up Perceptive Lighting from a config entry."""
    hass.data.setdefault(DOMAIN, {})
    await _async_register_frontend_cards_and_panel(hass)
    _async_register_services(hass)

    hub = PerceptiveLightingHub(hass, entry)
    await hub.async_setup()
    hass.data[DOMAIN][entry.entry_id] = hub

    await hass.config_entries.async_forward_entry_setups(entry, PLATFORMS)
    entry.async_on_unload(entry.add_update_listener(_async_update_listener))
    return True


async def _async_update_listener(hass: HomeAssistant, entry: ConfigEntry) -> None:
    """Reload config entry when options change."""
    await hass.config_entries.async_reload(entry.entry_id)


async def async_unload_entry(hass: HomeAssistant, entry: ConfigEntry) -> bool:
    """Unload a config entry."""
    unload_ok = await hass.config_entries.async_unload_platforms(entry, PLATFORMS)
    if unload_ok:
        hub: PerceptiveLightingHub | None = hass.data[DOMAIN].pop(
            entry.entry_id, None
        )
        if hub:
            await hub.async_unload()
    return unload_ok


async def _async_register_frontend_cards_and_panel(hass: HomeAssistant) -> None:
    """Serve and auto-register the Custom Sidebar Panel and bundled Lovelace Cards."""
    frontend_dir = Path(__file__).parent / "frontend"
    card_path = frontend_dir / CARD_JS_FILENAME
    if not card_path.exists():
        _LOGGER.warning("Bundled Lovelace card file not found at %s", card_path)
        return

    url_path = f"{STATIC_URL_PATH}/{CARD_JS_FILENAME}"
    versioned_url = f"{url_path}?v={VERSION}"

    if not hass.data[DOMAIN].get("_static_registered"):
        try:
            await hass.http.async_register_static_paths(
                [StaticPathConfig(STATIC_URL_PATH, str(frontend_dir), cache_headers=False)]
            )
        except AttributeError:
            hass.http.register_static_path(
                STATIC_URL_PATH, str(frontend_dir), cache_headers=False
            )
        except RuntimeError:
            pass
        hass.data[DOMAIN]["_static_registered"] = True

    if hass.data[DOMAIN].get("_frontend_version") != VERSION:
        add_extra_js_url(hass, versioned_url)

        # Register Custom Sidebar Panel in Home Assistant's Left Navigation Menu
        try:
            frontend.async_register_built_in_panel(
                hass,
                component_name="custom",
                sidebar_title=SIDEBAR_PANEL_TITLE,
                sidebar_icon=SIDEBAR_PANEL_ICON,
                frontend_url_path=SIDEBAR_PANEL_URL_PATH,
                config={
                    "_panel_custom": {
                        "name": SIDEBAR_PANEL_COMPONENT_NAME,
                        "module_url": versioned_url,
                        "embed_iframe": False,
                        "trust_external": False,
                    }
                },
                require_admin=False,
                update=True,
            )
        except Exception as err:  # noqa: BLE001
            _LOGGER.warning("Could not register Perceptive Lighting sidebar panel: %s", err)

        # Also register in Lovelace ResourceStorageCollection so the visual Card Picker always loads it as an ES module
        try:
            lovelace = hass.data.get("lovelace")
            resources = getattr(lovelace, "resources", None)
            if resources is None and isinstance(lovelace, dict):
                resources = lovelace.get("resources")
            if resources is not None:
                if not getattr(resources, "loaded", True):
                    await resources.async_load()
                existing_items = list(resources.async_items() or [])
                matched = None
                for item in existing_items:
                    if STATIC_URL_PATH in item.get("url", ""):
                        matched = item
                        break
                if matched is None:
                    await resources.async_create_item(
                        {"res_type": "module", "url": versioned_url}
                    )
                elif matched.get("url") != versioned_url:
                    await resources.async_update_item(
                        matched["id"], {"res_type": "module", "url": versioned_url}
                    )
        except Exception as err:  # noqa: BLE001
            _LOGGER.debug("Could not auto-register in Lovelace resources storage: %s", err)

        hass.data[DOMAIN]["_frontend_version"] = VERSION
        _LOGGER.info(
            "Registered Perceptive Lighting sidebar panel (/perceptive-lighting) and Lovelace cards at %s",
            versioned_url,
        )


def _find_room(hass: HomeAssistant, room_key: str) -> RoomController | None:
    """Find a RoomController by room_id, area_id, or room_name across active hubs."""
    key_low = str(room_key).lower().strip()
    for val in hass.data.get(DOMAIN, {}).values():
        if isinstance(val, PerceptiveLightingHub):
            if room_key in val.rooms:
                return val.rooms[room_key]
            for rctrl in val.rooms.values():
                if (
                    rctrl.room_id.lower() == key_low
                    or (rctrl.area_id and rctrl.area_id.lower() == key_low)
                    or rctrl.room_name.lower() == key_low
                ):
                    return rctrl
    return None


def _async_register_services(hass: HomeAssistant) -> None:
    """Register Perceptive Lighting domain services/actions."""
    if hass.services.has_service(DOMAIN, SERVICE_UPDATE_ROOM_CONFIG):
        return

    async def handle_apply_adaptive_scene(call: ServiceCall) -> None:
        room = _find_room(hass, call.data["room_id"])
        if room:
            await room.async_select_scene_or_activity(call.data["scene"])

    async def handle_capture_adaptive_scene(call: ServiceCall) -> None:
        room = _find_room(hass, call.data["room_id"])
        if room:
            await room.async_capture_current_as_adaptive_scene(
                name=call.data["name"],
                activity=call.data.get("activity"),
            )

    async def handle_delete_adaptive_scene(call: ServiceCall) -> None:
        room = _find_room(hass, call.data["room_id"])
        if room:
            sid = call.data["scene_id"]
            room.adaptive_scenes.pop(sid, None)
            await room.hub.async_save_runtime_state()
            room.async_evaluate_room(room.hub.outdoor_state, trigger_control=False)

    async def handle_lock_room(call: ServiceCall) -> None:
        room = _find_room(hass, call.data["room_id"])
        if room:
            await room.async_set_automation_lock(True)

    async def handle_unlock_room(call: ServiceCall) -> None:
        room = _find_room(hass, call.data["room_id"])
        if room:
            await room.async_set_automation_lock(False)

    async def handle_nudge_feel(call: ServiceCall) -> None:
        room = _find_room(hass, call.data["room_id"])
        if room:
            await room.async_nudge_feel(
                direction=call.data.get("direction", "more_light"),
                step_pct=float(call.data.get("step_pct", 10.0)),
            )

    async def handle_calibrate_feels_dark_now(call: ServiceCall) -> None:
        room = _find_room(hass, call.data["room_id"])
        if room:
            await room.async_calibrate_feels_dark_now()

    async def handle_calibrate_feels_bright_now(call: ServiceCall) -> None:
        room = _find_room(hass, call.data["room_id"])
        if room:
            await room.async_calibrate_feels_bright_now()

    async def handle_set_window_compass(call: ServiceCall) -> None:
        room = _find_room(hass, call.data["room_id"])
        if room:
            await room.async_set_window_compass(float(call.data["azimuth"]))

    async def handle_update_room_exclusions(call: ServiceCall) -> None:
        room = _find_room(hass, call.data["room_id"])
        if room:
            excluded = list(call.data.get("excluded_entities", []))
            await room.async_update_room_config({CONF_EXCLUDED_ENTITIES: excluded})

    async def handle_relearn_history(call: ServiceCall) -> None:
        for val in hass.data.get(DOMAIN, {}).values():
            if isinstance(val, PerceptiveLightingHub):
                await val.async_run_historical_learning()

    async def handle_optimize_with_ai(call: ServiceCall) -> None:
        room_key = call.data.get("room_id")
        if room_key:
            room = _find_room(hass, room_key)
            if room:
                await room.async_optimize_with_gemini()
        else:
            for val in hass.data.get(DOMAIN, {}).values():
                if isinstance(val, PerceptiveLightingHub):
                    for room in val.rooms.values():
                        await room.async_optimize_with_gemini()

    async def handle_reset_room(call: ServiceCall) -> None:
        room_key = call.data.get("room_id")
        if not room_key or str(room_key).lower().strip() == "all":
            for val in hass.data.get(DOMAIN, {}).values():
                if isinstance(val, PerceptiveLightingHub):
                    await val.async_reset_all_rooms()
        else:
            room = _find_room(hass, room_key)
            if room:
                await room.async_reset_room(relearn_history=True)

    async def handle_update_room_config(call: ServiceCall) -> None:
        room_key = call.data.get("room_id")
        if not room_key:
            return
        room = _find_room(hass, room_key)
        if room:
            updates = {k: v for k, v in call.data.items() if k != "room_id"}
            await room.async_update_room_config(updates)

    async def handle_update_hub_config(call: ServiceCall) -> None:
        updates = dict(call.data)
        for val in hass.data.get(DOMAIN, {}).values():
            if isinstance(val, PerceptiveLightingHub):
                await val.async_update_hub_config(updates)

    hass.services.async_register(
        DOMAIN,
        SERVICE_APPLY_ADAPTIVE_SCENE,
        handle_apply_adaptive_scene,
        schema=vol.Schema(
            {
                vol.Required("room_id"): str,
                vol.Required("scene"): str,
            }
        ),
    )
    hass.services.async_register(
        DOMAIN,
        SERVICE_CAPTURE_ADAPTIVE_SCENE,
        handle_capture_adaptive_scene,
        schema=vol.Schema(
            {
                vol.Required("room_id"): str,
                vol.Required("name"): str,
                vol.Optional("activity"): str,
            }
        ),
    )
    hass.services.async_register(
        DOMAIN,
        SERVICE_DELETE_ADAPTIVE_SCENE,
        handle_delete_adaptive_scene,
        schema=vol.Schema(
            {
                vol.Required("room_id"): str,
                vol.Required("scene_id"): str,
            }
        ),
    )
    hass.services.async_register(
        DOMAIN,
        SERVICE_LOCK_ROOM,
        handle_lock_room,
        schema=vol.Schema({vol.Required("room_id"): str}),
    )
    hass.services.async_register(
        DOMAIN,
        SERVICE_UNLOCK_ROOM,
        handle_unlock_room,
        schema=vol.Schema({vol.Required("room_id"): str}),
    )
    hass.services.async_register(
        DOMAIN,
        SERVICE_NUDGE_FEEL,
        handle_nudge_feel,
        schema=vol.Schema(
            {
                vol.Required("room_id"): str,
                vol.Required("direction"): str,
                vol.Optional("step_pct", default=10.0): vol.Coerce(float),
            }
        ),
    )
    hass.services.async_register(
        DOMAIN,
        SERVICE_CALIBRATE_FEELS_DARK_NOW,
        handle_calibrate_feels_dark_now,
        schema=vol.Schema({vol.Required("room_id"): str}),
    )
    hass.services.async_register(
        DOMAIN,
        SERVICE_CALIBRATE_FEELS_BRIGHT_NOW,
        handle_calibrate_feels_bright_now,
        schema=vol.Schema({vol.Required("room_id"): str}),
    )
    hass.services.async_register(
        DOMAIN,
        SERVICE_SET_WINDOW_COMPASS,
        handle_set_window_compass,
        schema=vol.Schema(
            {
                vol.Required("room_id"): str,
                vol.Required("azimuth"): vol.Coerce(float),
            }
        ),
    )
    hass.services.async_register(
        DOMAIN,
        SERVICE_UPDATE_ROOM_EXCLUSIONS,
        handle_update_room_exclusions,
        schema=vol.Schema(
            {
                vol.Required("room_id"): str,
                vol.Required("excluded_entities"): [str],
            }
        ),
    )
    hass.services.async_register(
        DOMAIN,
        SERVICE_RELEARN_HISTORY,
        handle_relearn_history,
        schema=vol.Schema({}),
    )
    hass.services.async_register(
        DOMAIN,
        SERVICE_OPTIMIZE_WITH_AI,
        handle_optimize_with_ai,
        schema=vol.Schema({vol.Optional("room_id"): str}),
    )
    hass.services.async_register(
        DOMAIN,
        SERVICE_RESET_ROOM,
        handle_reset_room,
        schema=vol.Schema({vol.Optional("room_id"): str}),
    )
    hass.services.async_register(
        DOMAIN,
        SERVICE_UPDATE_ROOM_CONFIG,
        handle_update_room_config,
        schema=vol.Schema({vol.Required("room_id"): str}, extra=vol.ALLOW_EXTRA),
    )
    hass.services.async_register(
        DOMAIN,
        SERVICE_UPDATE_HUB_CONFIG,
        handle_update_hub_config,
        schema=vol.Schema({}, extra=vol.ALLOW_EXTRA),
    )
