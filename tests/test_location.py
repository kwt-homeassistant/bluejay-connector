from __future__ import annotations

from datetime import timedelta, timezone
import importlib.util
from pathlib import Path
import sys
import types
import unittest


ROOT = Path(__file__).resolve().parents[1]
PACKAGE_ROOT = ROOT / "custom_components" / "aito"
LOCAL_TZ = timezone(timedelta(hours=8))


def _load_module(name: str, path: Path):
    spec = importlib.util.spec_from_file_location(name, path)
    if spec is None or spec.loader is None:
        raise RuntimeError(f"Cannot load {name}")
    module = importlib.util.module_from_spec(spec)
    sys.modules[name] = module
    spec.loader.exec_module(module)
    return module


custom_components = types.ModuleType("custom_components")
custom_components.__path__ = [str(ROOT / "custom_components")]
sys.modules.setdefault("custom_components", custom_components)

aito = types.ModuleType("custom_components.aito")
aito.__path__ = [str(PACKAGE_ROOT)]
sys.modules.setdefault("custom_components.aito", aito)

location = _load_module(
    "custom_components.aito.location",
    PACKAGE_ROOT / "location.py",
)


class VehicleLocationTest(unittest.TestCase):
    def test_valid_coordinates_are_exposed_as_valid(self) -> None:
        reading = location.parse_vehicle_location(
            self._payload(valid_flag=1, latitude=12.345, longitude=67.89)
        )

        self.assertIsNotNone(reading)
        self.assertEqual(reading.coordinates, (12.345, 67.89))
        self.assertEqual(reading.quality, "valid")
        self.assertTrue(reading.valid)
        self.assertFalse(reading.stale)

    def test_invalid_flag_coordinates_are_exposed_as_last_known(self) -> None:
        reading = location.parse_vehicle_location(
            self._payload(valid_flag=0, latitude=12.345, longitude=67.89)
        )

        self.assertIsNotNone(reading)
        self.assertEqual(reading.coordinates, (12.345, 67.89))
        self.assertEqual(reading.quality, "last_known")
        self.assertFalse(reading.valid)
        self.assertTrue(reading.stale)

    def test_rejects_zero_zero_and_out_of_range_coordinates(self) -> None:
        invalid_pairs = (
            (0, 0),
            (90.0001, 0),
            (-90.0001, 0),
            (0, 180.0001),
            (0, -180.0001),
            (float("nan"), 0),
            (0, float("inf")),
        )

        for latitude, longitude in invalid_pairs:
            with self.subTest(latitude=latitude, longitude=longitude):
                self.assertIsNone(
                    location.parse_vehicle_location(
                        self._payload(
                            valid_flag=1,
                            latitude=latitude,
                            longitude=longitude,
                        )
                    )
                )

    def test_rejects_boolean_nonnumeric_and_missing_coordinates(self) -> None:
        invalid_pairs = (
            (True, 67.89),
            (12.345, False),
            ("12.345", 67.89),
            (12.345, "67.89"),
            (None, 67.89),
            (12.345, None),
        )

        for latitude, longitude in invalid_pairs:
            with self.subTest(latitude=latitude, longitude=longitude):
                self.assertIsNone(
                    location.parse_vehicle_location(
                        self._payload(
                            valid_flag=1,
                            latitude=latitude,
                            longitude=longitude,
                        )
                    )
                )

        self.assertIsNone(location.parse_vehicle_location({}))
        self.assertIsNone(location.parse_vehicle_location({"location": None}))
        self.assertIsNone(location.parse_vehicle_location({"location": {}}))
        self.assertIsNone(
            location.parse_vehicle_location({"location": {"location": None}})
        )

    def test_metadata_uses_cloud_observation_names_and_ha_localizer(self) -> None:
        payload = self._payload(valid_flag=0, latitude=12.345, longitude=67.89)
        payload["location"].update(
            {
                "sateliteNum": 0,
                "gpsTime": 4102444800000,
                "lastUpdatedAt": 4102444860000,
            }
        )
        payload["location"]["location"]["gpsPrivacySwitch"] = 1
        reading = location.parse_vehicle_location(payload)

        self.assertIsNotNone(reading)
        attributes = location.location_attributes(
            reading,
            as_local=lambda value: value.astimezone(LOCAL_TZ),
        )

        self.assertEqual(
            attributes,
            {
                "location_quality": "last_known",
                "location_valid": False,
                "location_stale": True,
                "gps_privacy_switch": 1,
                "gps_satellite_count": 0,
                "cloud_location_observed_at": "2100-01-01T08:00:00+08:00",
                "cloud_payload_updated_at": "2100-01-01T08:01:00+08:00",
            },
        )
        self.assertNotIn("gps_fix_time", attributes)

    def test_missing_optional_metadata_is_omitted(self) -> None:
        reading = location.parse_vehicle_location(
            self._payload(valid_flag="1", latitude=12.345, longitude=67.89)
        )

        self.assertIsNotNone(reading)
        attributes = location.location_attributes(reading, as_local=lambda value: value)
        self.assertEqual(
            attributes,
            {
                "location_quality": "valid",
                "location_valid": True,
                "location_stale": False,
            },
        )

    def test_attributes_do_not_duplicate_coordinates_or_identifiers(self) -> None:
        reading = location.parse_vehicle_location(
            self._payload(valid_flag=0, latitude=12.345, longitude=67.89)
        )

        self.assertIsNotNone(reading)
        attributes = location.location_attributes(reading, as_local=lambda value: value)
        forbidden = {
            "latitude",
            "longitude",
            "coordinates",
            "vehicle_id",
            "vin",
            "address",
        }
        self.assertTrue(forbidden.isdisjoint(attributes))

    @staticmethod
    def _payload(*, valid_flag, latitude, longitude) -> dict:
        return {
            "location": {
                "location": {
                    "validFlag": valid_flag,
                    "latitude": latitude,
                    "longitude": longitude,
                }
            }
        }


if __name__ == "__main__":
    unittest.main()
