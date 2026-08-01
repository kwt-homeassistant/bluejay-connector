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


class ChargeTelemetryTest(unittest.TestCase):
    def setUp(self) -> None:
        vehicle = models.Vehicle(
            id="test-vehicle",
            name="Test AITO",
            profile=models.VehicleProfile(
                enterprise_code="SERES",
                project_code="SERES-F3",
            ),
        )
        self.spec = devices.vehicle_spec_for(vehicle)
        if self.spec is None:
            self.fail("SERES-F3 vehicle spec is missing")

    def value(self, data: dict, key: str):
        sensor = next(sensor for sensor in self.spec.sensors if sensor.key == key)
        return devices.sensor_value(data, sensor)

    def test_uses_ac_channel_while_ac_charging(self) -> None:
        data = {
            "charge": {
                "acChargeStatus": 6,
                "dcChargeStatus": 0,
                "acChargeCurrent": -31.4,
                "chargeCurrent": 0,
                "chargeVoltage": 227,
            }
        }

        self.assertEqual(self.value(data, "charge_status"), "充电中")
        self.assertEqual(self.value(data, "charge_current"), 31.4)
        self.assertEqual(self.value(data, "charge_power"), 7.1)

    def test_uses_dc_channel_while_dc_charging(self) -> None:
        data = {
            "charge": {
                "acChargeStatus": 0,
                "dcChargeStatus": 6,
                "dcChargeCurrent": 180,
                "chargeVoltage": 400,
            }
        }

        self.assertEqual(self.value(data, "charge_status"), "充电中")
        self.assertEqual(self.value(data, "charge_current"), 180)
        self.assertEqual(self.value(data, "charge_power"), 72.0)

    def test_prefers_ac_current_when_both_channels_report_charging(self) -> None:
        data = {
            "charge": {
                "acChargeStatus": 6,
                "dcChargeStatus": 6,
                "acChargeCurrent": 20,
                "dcChargeCurrent": 200,
                "chargeVoltage": 220,
            }
        }

        self.assertEqual(self.value(data, "charge_current"), 20)
        self.assertEqual(self.value(data, "charge_power"), 4.4)

    def test_uses_app_charge_status_enum(self) -> None:
        expected = {
            0: "未充电",
            1: "已连接，未充电",
            5: "充电故障",
            6: "充电中",
            7: "充电已停止",
            18: "充电预热中",
            25: "等待预约充电",
        }

        for status, text in expected.items():
            with self.subTest(status=status):
                data = {
                    "charge": {
                        "acChargeStatus": status,
                        "dcChargeStatus": 0,
                        "acChargeCurrent": 30,
                        "chargeVoltage": 220,
                    }
                }
                self.assertEqual(self.value(data, "charge_status"), text)
                expected_current = 30 if status == 6 else 0
                self.assertEqual(self.value(data, "charge_current"), expected_current)

    def test_takes_maximum_ac_dc_status_like_the_app(self) -> None:
        data = {"charge": {"acChargeStatus": 1, "dcChargeStatus": 25}}

        self.assertEqual(self.value(data, "charge_status"), "等待预约充电")
        self.assertEqual(self.value(data, "charge_current"), 0)

    def test_falls_back_to_legacy_generic_fields(self) -> None:
        data = {
            "charge": {
                "chargeStatus": 6,
                "chargeCurrent": -20,
                "chargeVoltage": 230,
            }
        }

        self.assertEqual(self.value(data, "charge_status"), "充电中")
        self.assertEqual(self.value(data, "charge_current"), 20)
        self.assertEqual(self.value(data, "charge_power"), 4.6)

    def test_missing_or_invalid_status_does_not_become_not_charging(self) -> None:
        self.assertIsNone(self.value({"charge": {}}, "charge_status"))
        self.assertIsNone(self.value({"charge": {}}, "charge_current"))
        self.assertIsNone(
            self.value(
                {"charge": {"acChargeStatus": -1, "dcChargeStatus": -1}},
                "charge_status",
            )
        )


if __name__ == "__main__":
    unittest.main()
