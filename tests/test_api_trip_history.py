from __future__ import annotations

from datetime import date
import importlib.util
import json
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
api = _load_module("custom_components.aito.api", PACKAGE_ROOT / "api.py")


class DayTripRangeApiTest(unittest.TestCase):
    def test_builds_query_and_vehicle_header(self) -> None:
        captured = {}

        def transport(method, url, headers, body, timeout):
            captured.update(
                method=method,
                url=url,
                headers=headers,
                body=body,
                timeout=timeout,
            )
            return 200, {"Content-Type": "application/json"}, json.dumps({"trips": []}).encode()

        client = api.AitoApiClient(
            apig_base_url="https://example.invalid",
            apig_authorization="synthetic-authorization",
            ivcs_device_id="synthetic-device",
            transport=transport,
        )

        response = client.day_trip_range(
            "synthetic-vehicle",
            date(2099, 1, 1),
            "2099-01-07",
        )

        self.assertEqual(response, {"trips": []})
        self.assertEqual(captured["method"], "GET")
        self.assertEqual(
            captured["url"],
            "https://example.invalid/vdas/v1/report/day-trip-range?startDate=2099-01-01&endDate=2099-01-07",
        )
        self.assertEqual(captured["headers"]["X-Vehicle-Id"], "synthetic-vehicle")
        self.assertIsNone(captured["body"])

    def test_rejects_invalid_or_reversed_dates(self) -> None:
        client = api.AitoApiClient(
            apig_authorization="synthetic-authorization",
            transport=lambda *_args: (200, {}, b"{}"),
        )
        with self.assertRaises(ValueError):
            client.day_trip_range("synthetic-vehicle", "2099-01-08", "2099-01-07")
        with self.assertRaises(ValueError):
            client.day_trip_range("synthetic-vehicle", "2099/01/01", "2099-01-07")


if __name__ == "__main__":
    unittest.main()
