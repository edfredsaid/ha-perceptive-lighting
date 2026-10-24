"""Unit tests for the Perceptive Lighting psychophysical & architectural engine."""
from __future__ import annotations

import sys
import types
from pathlib import Path
import unittest

# Allow importing engine.py and const.py directly without full Home Assistant core installed
ROOT = Path(__file__).resolve().parents[1]
PKG_DIR = ROOT / "custom_components" / "perceptive_lighting"
pkg = types.ModuleType("perceptive_lighting")
pkg.__path__ = [str(PKG_DIR)]
sys.modules["perceptive_lighting"] = pkg

from perceptive_lighting.const import (  # noqa: E402
    ACTIVITY_FILM,
    ACTIVITY_RELAXING,
    ACTIVITY_WORKING,
    DAYLIGHT_HIGH,
    DAYLIGHT_MEDIUM,
    DAYLIGHT_NONE,
    STATE_BRIGHT,
    STATE_COMFORTABLE,
    STATE_DARK,
    STATE_DIM,
    SUN_DIR_COMPASS,
    SUN_DIR_NEVER,
)
from perceptive_lighting.engine import (  # noqa: E402
    SensorLearnedProfile,
    compute_outdoor_environment,
    compute_room_perceptive_illuminance,
    estimate_synthetic_outdoor_lux,
)


class TestPerceptiveEngine(unittest.TestCase):
    """Test suite for Perceptive Lighting calculations."""

    def test_outdoor_cave_effect_adaptation(self) -> None:
        """Verify that 100 lx indoors feels comfortable at dusk/night, but dim on a blazing summer midday."""
        # 1. Night / Dusk (0 lx outside)
        outdoor_night = compute_outdoor_environment(
            sensor_readings={"sensor.outdoor_lux": 2.0},
            sensor_profiles={},
            weather_condition="clear-night",
            weather_cloud_coverage=10.0,
            solar_elevation=-12.0,
            solar_azimuth=180.0,
        )
        res_night = compute_room_perceptive_illuminance(
            indoor_sensor_readings={"sensor.living_lux": 100.0},
            sensor_profiles={},
            outdoor=outdoor_night,
            activity_key=ACTIVITY_RELAXING,
            daylight_level=DAYLIGHT_HIGH,
        )

        # 2. Blazing Sunny Summer Day (65,000 lx outside)
        outdoor_sunny = compute_outdoor_environment(
            sensor_readings={"sensor.outdoor_lux": 65000.0},
            sensor_profiles={},
            weather_condition="sunny",
            weather_cloud_coverage=5.0,
            solar_elevation=58.0,
            solar_azimuth=180.0,
        )
        res_sunny = compute_room_perceptive_illuminance(
            indoor_sensor_readings={"sensor.living_lux": 100.0},
            sensor_profiles={},
            outdoor=outdoor_sunny,
            activity_key=ACTIVITY_RELAXING,
            daylight_level=DAYLIGHT_HIGH,
        )

        # At night, 100 lx should feel Bright/Comfortable with 0% deficit
        self.assertGreaterEqual(res_night.perceptive_illuminance_pct, 80.0)
        self.assertEqual(res_night.lighting_deficit_pct, 0.0)
        self.assertIn(res_night.perceptive_state, (STATE_COMFORTABLE, STATE_BRIGHT))

        # On a 65,000 lx day with high windows, 100 lx indoors should have a much higher target and lower perceived %
        self.assertGreater(res_sunny.target_comfort_lux, res_night.target_comfort_lux * 3.0)
        self.assertLess(res_sunny.perceptive_illuminance_pct, res_night.perceptive_illuminance_pct)
        self.assertGreater(res_sunny.lighting_deficit_pct, 10.0)

    def test_diffuse_overcast_vs_direct_solar_glare(self) -> None:
        """Verify that diffuse overcast sky light produces higher spatial brightness than direct sun glare at the same sensor reading."""
        outdoor_cloudy = compute_outdoor_environment(
            sensor_readings={"sensor.outdoor_lux": 5000.0},
            sensor_profiles={},
            weather_condition="cloudy",
            weather_cloud_coverage=90.0,
            solar_elevation=25.0,
            solar_azimuth=180.0,
        )
        res_diffuse = compute_room_perceptive_illuminance(
            indoor_sensor_readings={"sensor.living_lux": 120.0},
            sensor_profiles={},
            outdoor=outdoor_cloudy,
            activity_key=ACTIVITY_RELAXING,
            daylight_level=DAYLIGHT_MEDIUM,
            sun_window_direction=SUN_DIR_NEVER,
        )

        outdoor_sunny = compute_outdoor_environment(
            sensor_readings={"sensor.outdoor_lux": 5000.0},
            sensor_profiles={},
            weather_condition="sunny",
            weather_cloud_coverage=5.0,
            solar_elevation=25.0,
            solar_azimuth=180.0,
        )
        res_direct_glare = compute_room_perceptive_illuminance(
            indoor_sensor_readings={"sensor.living_lux": 120.0},
            sensor_profiles={},
            outdoor=outdoor_sunny,
            activity_key=ACTIVITY_RELAXING,
            daylight_level=DAYLIGHT_MEDIUM,
            sun_window_direction=SUN_DIR_COMPASS,
            window_azimuth=180.0,
        )

        self.assertTrue(res_direct_glare.direct_sun_in_window)
        self.assertGreater(res_diffuse.effective_spatial_lux, res_direct_glare.effective_spatial_lux)
        self.assertGreater(res_diffuse.perceptive_illuminance_pct, res_direct_glare.perceptive_illuminance_pct)

    def test_multi_purpose_room_activities(self) -> None:
        """Verify that switching a room from Chilling to Working or Film changes target lux and CCT."""
        outdoor = compute_outdoor_environment(
            sensor_readings={"sensor.outdoor_lux": 1500.0},
            sensor_profiles={},
            weather_condition="cloudy",
            weather_cloud_coverage=80.0,
            solar_elevation=18.0,
            solar_azimuth=160.0,
        )
        res_work = compute_room_perceptive_illuminance(
            indoor_sensor_readings={"sensor.bedroom_lux": 60.0},
            sensor_profiles={},
            outdoor=outdoor,
            activity_key=ACTIVITY_WORKING,
        )
        res_chill = compute_room_perceptive_illuminance(
            indoor_sensor_readings={"sensor.bedroom_lux": 60.0},
            sensor_profiles={},
            outdoor=outdoor,
            activity_key=ACTIVITY_RELAXING,
        )
        res_film = compute_room_perceptive_illuminance(
            indoor_sensor_readings={"sensor.bedroom_lux": 60.0},
            sensor_profiles={},
            outdoor=outdoor,
            activity_key=ACTIVITY_FILM,
        )

        # Working requires much higher lux & cooler CCT than Chilling, which requires more than Film
        self.assertGreater(res_work.target_comfort_lux, res_chill.target_comfort_lux)
        self.assertGreater(res_chill.target_comfort_lux, res_film.target_comfort_lux)
        self.assertGreater(res_work.lighting_deficit_pct, res_chill.lighting_deficit_pct)
        self.assertGreater(res_work.recommended_cct_kelvin, res_chill.recommended_cct_kelvin)

    def test_natural_vs_total_perceptive_illuminance(self) -> None:
        """Verify that turning on room lights boosts Total Perceptive Illuminance while keeping Natural Perceptive Illuminance low."""
        outdoor = compute_outdoor_environment(
            sensor_readings={"sensor.outdoor_lux": 100.0},
            sensor_profiles={},
            weather_condition="cloudy",
            weather_cloud_coverage=90.0,
            solar_elevation=2.0,
            solar_azimuth=240.0,
        )
        # Before lights turn on: 12 lx natural daylight
        res_lights_off = compute_room_perceptive_illuminance(
            indoor_sensor_readings={"sensor.living_lux": 12.0},
            sensor_profiles={},
            outdoor=outdoor,
            artificial_lux_estimate=0.0,
        )
        # After lights turn on (+85 lx from lamps -> sensor now reads 97 lx)
        res_lights_on = compute_room_perceptive_illuminance(
            indoor_sensor_readings={"sensor.living_lux": 97.0},
            sensor_profiles={},
            outdoor=outdoor,
            artificial_lux_estimate=85.0,
        )

        # Total perceptive illuminance rises to comfortable/bright
        self.assertGreater(
            res_lights_on.perceptive_illuminance_pct,
            res_lights_off.perceptive_illuminance_pct + 25.0,
        )
        # Natural perceptive illuminance stays non-zero and well below total perceptive illuminance
        self.assertGreater(res_lights_on.natural_perceptive_illuminance_pct, 10.0)
        self.assertLess(
            res_lights_on.natural_perceptive_illuminance_pct,
            res_lights_on.perceptive_illuminance_pct - 20.0,
        )

    def test_multi_sensor_internal_contrast_gradient(self) -> None:
        """Verify that multiple sensors in a room detect internal contrast and per-sensor placement calibration."""
        outdoor = compute_outdoor_environment(
            sensor_readings={"sensor.outdoor_lux": 10000.0},
            sensor_profiles={},
            weather_condition="partlycloudy",
            weather_cloud_coverage=40.0,
            solar_elevation=30.0,
            solar_azimuth=180.0,
        )
        profiles = {
            "sensor.window_lux": SensorLearnedProfile(
                entity_id="sensor.window_lux", placement="near_window"
            ),
            "sensor.corner_lux": SensorLearnedProfile(
                entity_id="sensor.corner_lux", placement="dim_corner"
            ),
        }
        res = compute_room_perceptive_illuminance(
            indoor_sensor_readings={
                "sensor.window_lux": 600.0,
                "sensor.corner_lux": 20.0,
            },
            sensor_profiles=profiles,
            outdoor=outdoor,
        )
        self.assertGreater(res.internal_contrast_ratio, 3.0)
        # Geometric blend ensures the 600 lx window sensor doesn't trick the room into thinking the dark corner is 310 lx
        self.assertLess(res.effective_spatial_lux, 200.0)


if __name__ == "__main__":
    unittest.main()
