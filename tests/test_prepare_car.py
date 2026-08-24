from __future__ import annotations

import asyncio
from datetime import datetime, timedelta, timezone
import hashlib
import importlib.util
from pathlib import Path
import sys
import types
import unittest
from unittest.mock import patch


ROOT = Path(__file__).resolve().parents[1]
PACKAGE_ROOT = ROOT / "custom_components" / "aito"
SHANGHAI = timezone(timedelta(hours=8))


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

const = _load_module("custom_components.aito.const", PACKAGE_ROOT / "const.py")
api = _load_module("custom_components.aito.api", PACKAGE_ROOT / "api.py")
prepare_car = _load_module("custom_components.aito.prepare_car", PACKAGE_ROOT / "prepare_car.py")


homeassistant = types.ModuleType("homeassistant")
homeassistant.__path__ = []
sys.modules.setdefault("homeassistant", homeassistant)

ha_config_entries = types.ModuleType("homeassistant.config_entries")
ha_config_entries.ConfigEntry = type("ConfigEntry", (), {})
sys.modules.setdefault("homeassistant.config_entries", ha_config_entries)

ha_core = types.ModuleType("homeassistant.core")
ha_core.HomeAssistant = type("HomeAssistant", (), {})
sys.modules.setdefault("homeassistant.core", ha_core)


class _DataUpdateCoordinator:
    @classmethod
    def __class_getitem__(cls, _item):
        return cls


ha_update_coordinator = types.ModuleType("homeassistant.helpers.update_coordinator")
ha_update_coordinator.DataUpdateCoordinator = _DataUpdateCoordinator
sys.modules.setdefault("homeassistant.helpers.update_coordinator", ha_update_coordinator)

ha_exceptions = types.ModuleType("homeassistant.exceptions")
ha_exceptions.ConfigEntryAuthFailed = type("ConfigEntryAuthFailed", (RuntimeError,), {})
sys.modules.setdefault("homeassistant.exceptions", ha_exceptions)

ha_dt = types.ModuleType("homeassistant.util.dt")
ha_dt.now = lambda: datetime.now(timezone.utc)
sys.modules.setdefault("homeassistant.util.dt", ha_dt)

ha_util = types.ModuleType("homeassistant.util")
ha_util.__path__ = []
ha_util.dt = ha_dt
sys.modules.setdefault("homeassistant.util", ha_util)

coordinator = _load_module("custom_components.aito.coordinator", PACKAGE_ROOT / "coordinator.py")


class PrepareCarTrackerTest(unittest.TestCase):
    def test_tracks_pending_confirmation_and_cooldown(self) -> None:
        tracker = prepare_car.PrepareCarCommandTracker(cooldown_seconds=60)
        requested_at = datetime(2026, 8, 24, 7, 0, tzinfo=SHANGHAI)

        tracker.begin(
            "vehicle",
            enabled=True,
            now_monotonic=100.0,
            requested_at=requested_at,
        )
        tracker.mark_pending("vehicle")
        tracker.record_readback("vehicle", 2)
        snapshot = tracker.snapshot("vehicle", now_monotonic=112.2)

        self.assertEqual(snapshot["command_state"], "pending_confirmation")
        self.assertEqual(snapshot["command_action"], "enable")
        self.assertEqual(snapshot["command_requested_at"], "2026-08-24T07:00:00+08:00")
        self.assertEqual(snapshot["command_cooldown_remaining_seconds"], 48)
        self.assertEqual(snapshot["command_readback_attempts"], 2)

        with self.assertRaises(prepare_car.PrepareCarCooldownError):
            tracker.begin(
                "vehicle",
                enabled=False,
                now_monotonic=159.9,
                requested_at=requested_at,
            )

    def test_confirms_or_fails_without_sensitive_attributes(self) -> None:
        tracker = prepare_car.PrepareCarCommandTracker(cooldown_seconds=1)
        requested_at = datetime(2026, 8, 24, 7, 0, tzinfo=SHANGHAI)
        tracker.begin("vehicle", enabled=False, now_monotonic=1.0, requested_at=requested_at)
        tracker.mark_confirmed(
            "vehicle",
            confirmed_at=datetime(2026, 8, 24, 7, 1, tzinfo=SHANGHAI),
        )
        confirmed = tracker.snapshot("vehicle", now_monotonic=2.0)
        self.assertEqual(confirmed["command_state"], "confirmed")
        self.assertEqual(confirmed["command_confirmed_at"], "2026-08-24T07:01:00+08:00")
        self.assertNotIn("vehicle_id", confirmed)

        tracker.begin("vehicle", enabled=True, now_monotonic=3.0, requested_at=requested_at)
        tracker.mark_failed("vehicle", error_code="READBACK_TIMEOUT")
        self.assertEqual(
            tracker.snapshot("vehicle", now_monotonic=3.1)["command_error_code"],
            "READBACK_TIMEOUT",
        )

    def test_default_plan_match_requires_plan_zero_and_status(self) -> None:
        enabled = {"departurePlan": {"departurePlanList": [{"planId": 0, "planStatus": 0}]}}
        disabled = {"departurePlan": {"departurePlanList": [{"planId": "0", "planStatus": 1}]}}
        unrelated = {"departurePlan": {"departurePlanList": [{"planId": 1, "planStatus": 0}]}}

        self.assertTrue(prepare_car.departure_plan_matches(enabled, enabled=True))
        self.assertTrue(prepare_car.departure_plan_matches(disabled, enabled=False))
        self.assertFalse(prepare_car.departure_plan_matches(unrelated, enabled=True))
        self.assertFalse(prepare_car.departure_plan_matches({}, enabled=False))


class ApigTlsPinTest(unittest.TestCase):
    def test_accepts_only_the_reviewed_leaf_digest(self) -> None:
        certificate = b"synthetic reviewed certificate"
        original = api.APIG_TLS_SHA256_FINGERPRINT
        api.APIG_TLS_SHA256_FINGERPRINT = hashlib.sha256(certificate).hexdigest()
        try:
            api._verify_apig_leaf_certificate(certificate)
            with self.assertRaises(api.AitoTlsPinError):
                api._verify_apig_leaf_certificate(b"different certificate")
        finally:
            api.APIG_TLS_SHA256_FINGERPRINT = original

    def test_rejects_nonfixed_host_before_connecting(self) -> None:
        with self.assertRaises(api.AitoTlsPinError):
            api._urllib_pinned_apig_transport(
                "GET",
                "https://example.invalid/vcam/v1/accounts/vehicles",
                {},
                None,
                1.0,
            )


class PrepareCarCoordinatorTest(unittest.IsolatedAsyncioTestCase):
    def _new_coordinator(self):
        instance = object.__new__(coordinator.AitoDataCoordinator)
        instance._prepare_car_lock = asyncio.Lock()
        instance._prepare_car_commands = prepare_car.PrepareCarCommandTracker(cooldown_seconds=60)
        instance.data = {"vehicle": {"battery": {"soc": 52}}}
        instance.client = types.SimpleNamespace(control_now_departure_plan=lambda *_args, **_kwargs: None)
        listener_states: list[str] = []

        def record_listener_update() -> None:
            listener_states.append(
                instance.prepare_car_command_attributes("vehicle")["command_state"]
            )

        instance.async_update_listeners = record_listener_update
        return instance, listener_states

    async def test_direct_command_waits_for_matching_readback_before_confirming(self) -> None:
        instance, listener_states = self._new_coordinator()
        requests: list[tuple[str, bool]] = []
        readbacks = [
            {"departurePlan": {"departurePlanList": [{"planId": 0, "planStatus": 1}]}},
            {"departurePlan": {"departurePlanList": [{"planId": 0, "planStatus": 0}]}},
        ]

        instance.client.control_now_departure_plan = (
            lambda vehicle_id, *, enabled: requests.append((vehicle_id, enabled))
        )

        async def apig_request(request, *args, retry_after_refresh=True):
            self.assertFalse(retry_after_refresh)
            request(*args)

        async def dynamic_infos(_vehicle_id, sections):
            self.assertEqual(sections, {"departurePlan": 0})
            return readbacks.pop(0)

        instance._async_apig_request = apig_request
        instance._async_dynamic_infos = dynamic_infos

        with patch.object(coordinator, "_PREPARE_CAR_READBACK_DELAY_SECONDS", 0):
            await instance.async_control_now_departure_plan("vehicle", enabled=True)

        attributes = instance.prepare_car_command_attributes("vehicle")
        self.assertEqual(requests, [("vehicle", True)])
        self.assertEqual(attributes["command_state"], "confirmed")
        self.assertEqual(attributes["command_action"], "enable")
        self.assertEqual(attributes["command_readback_attempts"], 2)
        self.assertTrue(attributes["command_requested_at"].endswith("+08:00"))
        self.assertTrue(attributes["command_confirmed_at"].endswith("+08:00"))
        self.assertEqual(instance.data["vehicle"]["battery"]["soc"], 52)
        self.assertEqual(
            instance.data["vehicle"]["departurePlan"]["departurePlanList"][0]["planStatus"],
            0,
        )
        self.assertIn("sending", listener_states)
        self.assertIn("pending_confirmation", listener_states)
        self.assertEqual(listener_states[-1], "confirmed")

    async def test_direct_command_marks_readback_timeout_as_failed(self) -> None:
        instance, _listener_states = self._new_coordinator()

        async def apig_request(_request, *_args, **_kwargs):
            return None

        async def dynamic_infos(_vehicle_id, _sections):
            return {"departurePlan": {"departurePlanList": []}}

        instance._async_apig_request = apig_request
        instance._async_dynamic_infos = dynamic_infos

        with (
            patch.object(coordinator, "_PREPARE_CAR_READBACK_ATTEMPTS", 2),
            patch.object(coordinator, "_PREPARE_CAR_READBACK_DELAY_SECONDS", 0),
        ):
            with self.assertRaises(coordinator.AitoCommandError) as raised:
                await instance.async_control_now_departure_plan("vehicle", enabled=False)

        self.assertEqual(raised.exception.result_code, "READBACK_TIMEOUT")
        attributes = instance.prepare_car_command_attributes("vehicle")
        self.assertEqual(attributes["command_state"], "failed")
        self.assertEqual(attributes["command_action"], "disable")
        self.assertEqual(attributes["command_error_code"], "READBACK_TIMEOUT")
        self.assertEqual(attributes["command_readback_attempts"], 2)

    async def test_global_lock_and_cooldown_allow_only_one_cloud_command(self) -> None:
        instance, _listener_states = self._new_coordinator()
        request_entered = asyncio.Event()
        release_request = asyncio.Event()
        request_count = 0

        async def apig_request(_request, *_args, **_kwargs):
            nonlocal request_count
            request_count += 1
            request_entered.set()
            await release_request.wait()

        async def dynamic_infos(_vehicle_id, _sections):
            return {"departurePlan": {"departurePlanList": [{"planId": 0, "planStatus": 0}]}}

        instance._async_apig_request = apig_request
        instance._async_dynamic_infos = dynamic_infos

        first = asyncio.create_task(
            instance.async_control_now_departure_plan("vehicle", enabled=True)
        )
        await asyncio.wait_for(request_entered.wait(), timeout=1)
        second = asyncio.create_task(
            instance.async_control_now_departure_plan("vehicle", enabled=True)
        )
        await asyncio.sleep(0)
        self.assertEqual(request_count, 1)
        release_request.set()
        await first

        with self.assertRaises(coordinator.AitoCommandError) as raised:
            await second
        self.assertEqual(raised.exception.result_code, "COOLDOWN_ACTIVE")
        self.assertEqual(request_count, 1)


if __name__ == "__main__":
    unittest.main()
