"""Constants for the Perceptive Lighting integration."""
from __future__ import annotations

from typing import Final

DOMAIN: Final = "perceptive_lighting"
NAME: Final = "Perceptive Lighting"
VERSION: Final = "1.1.0"

# Static frontend URL for bundled custom Lovelace cards
STATIC_URL_PATH: Final = "/perceptive_lighting_static"
CARD_JS_FILENAME: Final = "perceptive-lighting-cards.js"

# Hub / Global Configuration Keys
CONF_WEATHER_ENTITY: Final = "weather_entity"
CONF_OUTDOOR_SENSORS: Final = "outdoor_sensors"
CONF_SUN_ENTITY: Final = "sun_entity"
CONF_OUTDOOR_SENSOR_PROFILES: Final = "outdoor_sensor_profiles"
CONF_GLOBAL_LOCK: Final = "global_lock"

# Room Configuration Keys (Subentries / Room Entries)
CONF_ROOMS: Final = "rooms"
CONF_ROOM_ID: Final = "room_id"
CONF_ROOM_NAME: Final = "room_name"
CONF_AREA_ID: Final = "area_id"
CONF_OPERATING_MODE: Final = "operating_mode"
CONF_ACTIVITIES: Final = "activities"
CONF_DEFAULT_ACTIVITY: Final = "default_activity"
CONF_DAYLIGHT_LEVEL: Final = "daylight_level"
CONF_SUN_WINDOW_DIRECTION: Final = "sun_window_direction"
CONF_WINDOW_AZIMUTH: Final = "window_azimuth"
CONF_EXCLUDED_ENTITIES: Final = "excluded_entities"
CONF_INCLUDE_MEDIA_PLAYERS: Final = "include_media_players"
CONF_SENSOR_PROFILES: Final = "sensor_profiles"
CONF_CCT_MODE: Final = "cct_mode"
CONF_ON_OFF_COOLDOWN_MINUTES: Final = "on_off_cooldown_minutes"
CONF_ADJUST_INTERVAL_MINUTES: Final = "adjust_interval_minutes"
CONF_TRANSITION_SECONDS: Final = "transition_seconds"
CONF_LINKED_HA_SCENES: Final = "linked_ha_scenes"
CONF_ADAPTIVE_SCENES: Final = "adaptive_scenes"
CONF_LIGHT_GROUPS: Final = "light_groups"
CONF_ACTIVITY_TRIGGERS: Final = "activity_triggers"
CONF_BLOCKER_ENTITIES: Final = "blocker_entities"

# Operating Modes
MODE_SENSOR_ONLY: Final = "sensor_only"
MODE_SIMPLE_TRANSIT: Final = "simple_transit"
MODE_FULL_ADAPTIVE: Final = "full_adaptive"

OPERATING_MODES: Final = {
    MODE_SENSOR_ONLY: "Sensor Only (Manual Automations / Wasp-in-a-Box)",
    MODE_SIMPLE_TRANSIT: "Simple Controller (Hallway / Transit / Utility)",
    MODE_FULL_ADAPTIVE: "Full Adaptive Controller (Living / Dwelling / Multi-Use)",
}

# Room Activities (Multi-purpose room profiles)
ACTIVITY_RELAXING: Final = "relaxing"
ACTIVITY_WORKING: Final = "working"
ACTIVITY_READING: Final = "reading"
ACTIVITY_FILM: Final = "film"
ACTIVITY_SLEEP_PREP: Final = "sleep_prep"
ACTIVITY_TRANSIT: Final = "transit"
ACTIVITY_BRIGHT_TASK: Final = "bright_task"

ACTIVITY_DEFINITIONS: Final = {
    ACTIVITY_RELAXING: {
        "name": "Relaxing / Chilling",
        "icon": "mdi:sofa",
        "base_lux": 65.0,
        "adaptation_alpha": 45.0,
        "cave_contrast_weight": 0.85,
        "min_cct": 2200,
        "max_cct": 3200,
        "brightness_scale": 0.75,
    },
    ACTIVITY_WORKING: {
        "name": "Working / Focus",
        "icon": "mdi:laptop",
        "base_lux": 220.0,
        "adaptation_alpha": 85.0,
        "cave_contrast_weight": 1.25,
        "min_cct": 3200,
        "max_cct": 4800,
        "brightness_scale": 1.0,
    },
    ACTIVITY_READING: {
        "name": "Reading",
        "icon": "mdi:book-open-page-variant",
        "base_lux": 140.0,
        "adaptation_alpha": 60.0,
        "cave_contrast_weight": 1.0,
        "min_cct": 2600,
        "max_cct": 3800,
        "brightness_scale": 0.88,
    },
    ACTIVITY_FILM: {
        "name": "Watching Film / Media",
        "icon": "mdi:movie-open",
        "base_lux": 18.0,
        "adaptation_alpha": 10.0,
        "cave_contrast_weight": 0.25,
        "min_cct": 2000,
        "max_cct": 2600,
        "brightness_scale": 0.30,
    },
    ACTIVITY_SLEEP_PREP: {
        "name": "Wind-Down / Bedtime",
        "icon": "mdi:weather-night",
        "base_lux": 25.0,
        "adaptation_alpha": 15.0,
        "cave_contrast_weight": 0.40,
        "min_cct": 2000,
        "max_cct": 2400,
        "brightness_scale": 0.35,
    },
    ACTIVITY_TRANSIT: {
        "name": "Transit / Passing Through",
        "icon": "mdi:walk",
        "base_lux": 50.0,
        "adaptation_alpha": 35.0,
        "cave_contrast_weight": 0.75,
        "min_cct": 2400,
        "max_cct": 4000,
        "brightness_scale": 0.70,
    },
    ACTIVITY_BRIGHT_TASK: {
        "name": "Bright Task / Assembly",
        "icon": "mdi:spotlight-beam",
        "base_lux": 320.0,
        "adaptation_alpha": 100.0,
        "cave_contrast_weight": 1.35,
        "min_cct": 3800,
        "max_cct": 5200,
        "brightness_scale": 1.0,
    },
}

# Daylight Levels (Window Exposure)
DAYLIGHT_NONE: Final = "none"
DAYLIGHT_LOW: Final = "low"
DAYLIGHT_MEDIUM: Final = "medium"
DAYLIGHT_HIGH: Final = "high"

DAYLIGHT_LEVELS: Final = {
    DAYLIGHT_NONE: "None (Windowless / Internal Space)",
    DAYLIGHT_LOW: "Low (Small window, shaded, or faces North)",
    DAYLIGHT_MEDIUM: "Medium (Standard windows)",
    DAYLIGHT_HIGH: "High (Large windows, bi-folds, or skylights)",
}

DAYLIGHT_ADAPTATION_MULTIPLIERS: Final = {
    DAYLIGHT_NONE: 0.15,  # Minimal circadian expectation shift, no window contrast
    DAYLIGHT_LOW: 0.65,
    DAYLIGHT_MEDIUM: 1.0,
    DAYLIGHT_HIGH: 1.35,  # High simultaneous contrast near large glazing
}

# Direct Sun Window Timing
SUN_DIR_AUTO: Final = "auto_learn"
SUN_DIR_NEVER: Final = "never"
SUN_DIR_MORNING: Final = "morning_east"
SUN_DIR_MIDDAY: Final = "midday_south"
SUN_DIR_EVENING: Final = "evening_west"
SUN_DIR_COMPASS: Final = "compass_calibrated"

SUN_DIRECTIONS: Final = {
    SUN_DIR_AUTO: "Auto-Learn from Sensor History & Weather",
    SUN_DIR_NEVER: "Never / Indirect Sky Light Only",
    SUN_DIR_MORNING: "Morning Sun (East-Facing ~45°–135°)",
    SUN_DIR_MIDDAY: "Midday / Afternoon Sun (South-Facing ~135°–225°)",
    SUN_DIR_EVENING: "Late Afternoon / Evening Sun (West-Facing ~225°–315°)",
    SUN_DIR_COMPASS: "Calibrated via Phone Compass",
}

# Per-Sensor Placement Profiles
PLACEMENT_AUTO: Final = "auto_learn"
PLACEMENT_BALANCED: Final = "balanced"
PLACEMENT_NEAR_WINDOW: Final = "near_window"
PLACEMENT_DIM_CORNER: Final = "dim_corner"
PLACEMENT_CEILING: Final = "ceiling"

SENSOR_PLACEMENTS: Final = {
    PLACEMENT_AUTO: "Auto-Learn from Room & History",
    PLACEMENT_BALANCED: "Balanced / Middle of Room (1.0x)",
    PLACEMENT_NEAR_WINDOW: "Near or Facing Window (Dampens glare 0.65x)",
    PLACEMENT_DIM_CORNER: "Tucked in Dim Corner / Shelf (Boosts reading 1.45x)",
    PLACEMENT_CEILING: "Ceiling Mounted Downward (1.15x)",
}

PLACEMENT_MULTIPLIERS: Final = {
    PLACEMENT_AUTO: 1.0,
    PLACEMENT_BALANCED: 1.0,
    PLACEMENT_NEAR_WINDOW: 0.65,
    PLACEMENT_DIM_CORNER: 1.45,
    PLACEMENT_CEILING: 1.15,
}

# CCT Control Modes
CCT_EXTERNAL: Final = "external"
CCT_KRUITHOF: Final = "kruithof_circadian"
CCT_BRIGHTNESS_ONLY: Final = "brightness_only"

CCT_MODES: Final = {
    CCT_EXTERNAL: "Compatible / External (Let Adaptive Lighting or Scenes handle CCT)",
    CCT_KRUITHOF: "Built-in Kruithof & Circadian CCT (Warmer when dim, crisp when bright)",
    CCT_BRIGHTNESS_ONLY: "Brightness & Switch Only (Never modify color/CCT)",
}

# Perceptive States
STATE_DARK: Final = "dark"
STATE_DIM: Final = "dim"
STATE_COMFORTABLE: Final = "comfortable"
STATE_BRIGHT: Final = "bright"

# Default Anti-Flicker & Smoothing Constants
DEFAULT_ON_OFF_COOLDOWN_MINUTES: Final = 10
DEFAULT_ADJUST_INTERVAL_MINUTES: Final = 5
DEFAULT_TRANSITION_SECONDS: Final = 20
DEFAULT_SUBSTANTIAL_LUX_SHIFT_PCT: Final = 32.0
DEFAULT_MICRO_NUDGE_STEP_PCT: Final = 8.0

# Services / Actions
SERVICE_APPLY_ADAPTIVE_SCENE: Final = "apply_adaptive_scene"
SERVICE_CAPTURE_ADAPTIVE_SCENE: Final = "capture_adaptive_scene"
SERVICE_DELETE_ADAPTIVE_SCENE: Final = "delete_adaptive_scene"
SERVICE_LOCK_ROOM: Final = "lock_room"
SERVICE_UNLOCK_ROOM: Final = "unlock_room"
SERVICE_NUDGE_FEEL: Final = "nudge_feel"
SERVICE_CALIBRATE_FEELS_DARK_NOW: Final = "calibrate_feels_dark_now"
SERVICE_CALIBRATE_FEELS_BRIGHT_NOW: Final = "calibrate_feels_bright_now"
SERVICE_SET_WINDOW_COMPASS: Final = "set_window_compass"
SERVICE_UPDATE_ROOM_EXCLUSIONS: Final = "update_room_exclusions"
SERVICE_RELEARN_HISTORY: Final = "relearn_history"
