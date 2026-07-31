from __future__ import annotations

import importlib.util
from pathlib import Path
import sys
import types
import unittest


ROOT = Path(__file__).resolve().parents[1]
PACKAGE_ROOT = ROOT / "custom_components" / "aito"


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

_load_module("custom_components.aito.const", PACKAGE_ROOT / "const.py")
models = _load_module("custom_components.aito.models", PACKAGE_ROOT / "models.py")
devices = _load_module("custom_components.aito.devices", PACKAGE_ROOT / "devices.py")


class EnergyReportSensorTest(unittest.TestCase):
    def setUp(self) -> None:
        self.vehicle = models.Vehicle(
            id="test-vehicle",
            name="Test AITO",
            profile=models.VehicleProfile(
                enterprise_code="SERES",
                project_code="SERES-F3",
            ),
        )
        self.spec = devices.vehicle_spec_for(self.vehicle)
        if self.spec is None:
            self.fail("SERES-F3 vehicle spec is missing")

    def test_exposes_total_today_and_month_energy_values(self) -> None:
        data = {
            "energyReport": {
                "total": {
                    "avgPowerConsum": 18.2,
                    "avgFuelConsum": 5.6,
                    "totalPowerConsum": 999.0,
                    "totalFuelConsum": 888.0,
                },
                "today": {
                    "avgPowerConsum": 16.1,
                    "avgFuelConsum": 1.2,
                    "totalPowerConsum": 7.3,
                    "totalFuelConsum": 0.4,
                    "dayDate": "2099-01-02",
                },
                "thisMonth": {
                    "avgPowerConsum": 17.4,
                    "avgFuelConsum": 2.1,
                    "totalPowerConsum": 83.2,
                    "totalFuelConsum": 4.8,
                    "monthDate": "2099-01",
                },
            }
        }
        values = {
            sensor.key: devices.sensor_value(data, sensor)
            for sensor in self.spec.sensors
            if sensor.source == "energy_report"
        }

        self.assertEqual(
            values,
            {
                "average_power_consumption": 18.2,
                "average_fuel_consumption": 5.6,
                "today_average_power_consumption": 16.1,
                "today_average_fuel_consumption": 1.2,
                "today_total_power_consumption": 7.3,
                "today_total_fuel_consumption": 0.4,
                "month_average_power_consumption": 17.4,
                "month_average_fuel_consumption": 2.1,
                "month_total_power_consumption": 83.2,
                "month_total_fuel_consumption": 4.8,
            },
        )

    def test_rejects_no_data_sentinel(self) -> None:
        data = {
            "energyReport": {
                "total": {"avgPowerConsum": -1, "avgFuelConsum": -1},
                "today": {
                    "avgPowerConsum": -1,
                    "avgFuelConsum": -1,
                    "totalPowerConsum": -1,
                    "totalFuelConsum": -1,
                },
                "thisMonth": {
                    "avgPowerConsum": -1,
                    "avgFuelConsum": -1,
                    "totalPowerConsum": -1,
                    "totalFuelConsum": -1,
                },
            }
        }

        for sensor in self.spec.sensors:
            if sensor.source == "energy_report":
                self.assertIsNone(devices.sensor_value(data, sensor), sensor.key)

    def test_supported_models_share_energy_report_contract(self) -> None:
        energy_keys_by_model = []
        for project_code in ("SERES-F3", "SERES-X1"):
            vehicle = models.Vehicle(
                id=project_code,
                name=project_code,
                profile=models.VehicleProfile(
                    enterprise_code="SERES",
                    project_code=project_code,
                ),
            )
            spec = devices.vehicle_spec_for(vehicle)
            if spec is None:
                self.fail(f"{project_code} vehicle spec is missing")
            energy_keys_by_model.append(
                tuple(sensor.key for sensor in spec.sensors if sensor.source == "energy_report")
            )

        self.assertEqual(energy_keys_by_model[0], energy_keys_by_model[1])


class TripHistorySensorContractTest(unittest.TestCase):
    def test_supported_models_share_eight_private_summary_sensors(self) -> None:
        expected = (
            "last_trip_distance",
            "last_trip_started_at",
            "last_trip_duration",
            "today_trip_distance",
            "seven_day_trip_distance",
            "trip_report_updated_at",
            "trip_history_coverage_days",
            "trip_history_backfill_progress",
        )
        for project_code in ("SERES-F3", "SERES-X1"):
            vehicle = models.Vehicle(
                id=project_code,
                name=project_code,
                profile=models.VehicleProfile(
                    enterprise_code="SERES",
                    project_code=project_code,
                ),
            )
            spec = devices.vehicle_spec_for(vehicle)
            if spec is None:
                self.fail(f"{project_code} vehicle spec is missing")
            self.assertEqual(
                tuple(sensor.key for sensor in spec.sensors if sensor.source == "trip_history"),
                expected,
            )
            self.assertTrue(devices.has_trip_history_sensors(spec))


if __name__ == "__main__":
    unittest.main()
