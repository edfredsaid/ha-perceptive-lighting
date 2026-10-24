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
from perceptive_lighting.ai_learner import (  # noqa: E402
    LearnedRoomWeights,
    PerceptiveObservation,
    apply_online_sgd_step,
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

    def test_online_ml_and_factorial_weather_model_selection(self) -> None:
        """Verify that Factorial Backtesting swaps the Weather Model in the Day regime without corrupting Evening."""
        weights = LearnedRoomWeights()
        evening_before = weights.get_regime_spec("evening").to_dict()

        # Sunny afternoon in 'day' regime: Cave Contrast predicted 140 lx target (lights ON),
        # but user says "Feels Bright Enough Now (Turn Off)" at 75 lx natural indoors.
        obs_sunny = PerceptiveObservation(
            timestamp_utc="2026-10-24T14:30:00Z",
            event_type="feels_bright_now",
            time_regime="day",
            activity=ACTIVITY_RELAXING,
            indoor_raw_lux=75.0,
            natural_spatial_lux=75.0,
            outdoor_sky_lux=45000.0,
            weather_condition="sunny",
            cloud_cover_ratio=0.10,
            diffuse_sky_factor=0.88,
            solar_elevation=38.0,
            solar_azimuth=210.0,
            is_daytime=True,
            outdoor_temp_c=22.0,
            indoor_temp_c=22.5,
            indoor_humidity_pct=45.0,
            day_length_hours=14.5,
            direct_sun_in_window=True,
            internal_contrast_ratio=1.2,
            model_target_lux=140.0,
            model_deficit_pct=35.0,
            model_recommended_bri_pct=45,
            user_desired_lights_on=False,
            user_desired_bri_pct=0,
        )
        apply_online_sgd_step(weights, obs_sunny)

        day_spec = weights.get_regime_spec("day")
        # Factorial backtest should switch 'day' weather model away from cave_contrast (e.g. to gloom_compensation)
        self.assertNotEqual(day_spec.weather_model, "cave_contrast")
        # And 'evening' regime must remain completely untouched!
        self.assertEqual(weights.get_regime_spec("evening").to_dict(), evening_before)

    def test_high_floor_sensor_dynamic_weighting(self) -> None:
        """Verify that a sensor with a high dark floor gets 100% weight when bright, but tapers near 0 when dim."""
        high_floor_prof = SensorLearnedProfile(
            entity_id="sensor.high_floor_pir",
            floor_lux=1.5,
            dropout_knee_lux=16.0,
        )
        good_low_light_prof = SensorLearnedProfile(
            entity_id="sensor.opt3001_desk",
        )
        # During daytime (60 lx), high-floor sensor has full 1.0 weight
        self.assertEqual(high_floor_prof.compute_dynamic_weight(60.0), 1.0)
        # At dusk when high-floor sensor drops early to 0.5 lx, its weight tapers to 0.05
        self.assertLessEqual(high_floor_prof.compute_dynamic_weight(0.5), 0.06)

        outdoor = compute_outdoor_environment(
            sensor_readings={"sensor.outdoor_lux": 250.0},
            sensor_profiles={},
            weather_condition="cloudy",
            weather_cloud_coverage=80.0,
            solar_elevation=1.0,
            solar_azimuth=250.0,
        )
        res = compute_room_perceptive_illuminance(
            indoor_sensor_readings={
                "sensor.high_floor_pir": 0.0,
                "sensor.opt3001_desk": 24.0,
            },
            sensor_profiles={
                "sensor.high_floor_pir": high_floor_prof,
                "sensor.opt3001_desk": good_low_light_prof,
            },
            outdoor=outdoor,
        )
        # Because the 0.0 lx high-floor sensor is tapered down to 5% weight, spatial lux stays near 22-24 lx (not 12 lx!)
        self.assertGreater(res.effective_spatial_lux, 20.0)

    def test_seasonal_winter_vs_summer_and_min_artificial_floor(self) -> None:
        """Verify seasonal/thermal bioclimatic model and minimum artificial light floor turn-on/off logic."""
        outdoor_winter = compute_outdoor_environment(
            sensor_readings={"sensor.outdoor_lux": 4000.0},
            sensor_profiles={},
            weather_condition="cloudy",
            weather_cloud_coverage=90.0,
            solar_elevation=16.0,
            solar_azimuth=180.0,
            outdoor_temp_c=2.0,
            day_length_hours=8.0,
            local_hour=13.0,
        )
        outdoor_summer = compute_outdoor_environment(
            sensor_readings={"sensor.outdoor_lux": 4000.0},
            sensor_profiles={},
            weather_condition="cloudy",
            weather_cloud_coverage=90.0,
            solar_elevation=16.0,
            solar_azimuth=180.0,
            outdoor_temp_c=26.0,
            day_length_hours=16.5,
            local_hour=13.0,
        )
        res_winter = compute_room_perceptive_illuminance(
            indoor_sensor_readings={"sensor.room_lux": 50.0},
            sensor_profiles={},
            outdoor=outdoor_winter,
            season_model="combined_bioclimatic",
            indoor_temp_c=18.5,
        )
        res_summer = compute_room_perceptive_illuminance(
            indoor_sensor_readings={"sensor.room_lux": 50.0},
            sensor_profiles={},
            outdoor=outdoor_summer,
            season_model="combined_bioclimatic",
            indoor_temp_c=24.0,
        )
        # Cold short-day winter requires significantly higher comfort target than hot long-day summer
        self.assertGreater(res_winter.target_comfort_lux, res_summer.target_comfort_lux * 1.25)

        # Verify Minimum Artificial Light Floor: if natural lux is 96 lx and target is 100 lx (4 lx deficit),
        # and min_artificial_lux is 18 lx, lights should NOT turn on and SHOULD turn off if currently on!
        res_small_def = compute_room_perceptive_illuminance(
            indoor_sensor_readings={"sensor.room_lux": 82.0},
            sensor_profiles={},
            outdoor=outdoor_summer,
            season_model="combined_bioclimatic",
            indoor_temp_c=24.0,
            min_artificial_lux=20.0,
        )
        self.assertFalse(res_small_def.should_turn_lights_on)
        self.assertTrue(res_small_def.should_turn_lights_off_while_occupied)


if __name__ == "__main__":
    unittest.main()
