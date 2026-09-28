"""Synthetic contract and failure-path tests; never connects to a vehicle."""
from __future__ import annotations

import asyncio
from datetime import datetime, timedelta
import importlib
import json
import sys
import types
import unittest
from unittest.mock import AsyncMock, Mock, patch

# Reuse the repository's lightweight HA harness (no running HA required).
from test_prepare_car import api, coordinator

charge_limit = importlib.import_module('custom_components.aito.charge_limit')
TZ = coordinator._SHANGHAI_TZ


def raw_frame(*, target=80, soc=42, age=30, ac=3, dc=1, status=25):
    return {
        'vehicleStatus': {'connectStatus': 1, 'lastUpdatedAt':
            int((datetime.now(TZ) - timedelta(seconds=age)).timestamp() * 1000)},
        'charge': {'maxSocPercent': target, 'soc': soc, 'chargeConStatus': ac,
            'dcChargeGunConnectStatus': dc, 'acChargeStatus': status, 'dcChargeStatus': status},
    }


class ChargeLimitApiTest(unittest.TestCase):
    def client(self, response='synthetic-command'):
        calls = []
        def transport(method, url, headers, body, timeout):
            calls.append((method, url, headers, body))
            return 200, {'Content-Type': 'application/json'}, json.dumps(response).encode()
        client = api.AitoApiClient(apig_base_url='https://example.invalid',
            apig_authorization='synthetic', transport=transport)
        client._wait_for_command = Mock()
        return client, calls

    def test_exact_put_query_and_command_poll(self):
        for target in (90, 95, 100):
            with self.subTest(target=target):
                client, calls = self.client()
                client.control_charge_default('synthetic-vehicle', target)
                self.assertEqual(len(calls), 1)
                method, url, headers, body = calls[0]
                self.assertEqual(method, 'PUT')
                self.assertEqual(url, 'https://example.invalid/vctrl/v1/controls/charging/percentage?chargePercentageMax=' + str(target))
                self.assertEqual(headers['X-Vehicle-Id'], 'synthetic-vehicle')
                self.assertIsNone(body)
                client._wait_for_command.assert_called_once_with('synthetic-vehicle', 'synthetic-command')

    def test_rejects_nonapproved_and_noninteger_targets_without_request(self):
        client, calls = self.client()
        for target in (0, 50, 89, 91, 101, True, 95.0, '95', None):
            with self.subTest(target=target), self.assertRaises(ValueError):
                client.control_charge_default('synthetic-vehicle', target)
        self.assertEqual(calls, [])

    def test_invalid_command_response_never_polls_or_retries(self):
        for response in (None, '', {}, 123):
            with self.subTest(response=response):
                client, calls = self.client(response)
                with self.assertRaises(api.AitoCommandError):
                    client.control_charge_default('synthetic-vehicle', 95)
                self.assertEqual(len(calls), 1)
                client._wait_for_command.assert_not_called()


class ChargeSnapshotTest(unittest.TestCase):
    def test_modes_and_explicit_disconnect(self):
        for ac, dc, connected, mode in ((1,1,False,None),(3,1,True,'ac'),(1,2,True,'dc')):
            frame = charge_limit.charge_snapshot(raw_frame(ac=ac, dc=dc))
            self.assertTrue(frame['valid'])
            self.assertEqual((frame['connected'],frame['mode']), (connected,mode))

    def test_offline_stale_future_and_ambiguous_frames_fail_closed(self):
        frames = [raw_frame(age=121), raw_frame(age=-10), raw_frame(ac=3,dc=2),
                  raw_frame(ac=2), raw_frame(target=float('nan')), raw_frame(soc=101)]
        offline = raw_frame()
        offline['vehicleStatus']['connectStatus'] = 0
        frames.append(offline)
        for frame in frames:
            with self.subTest(frame=frame):
                self.assertFalse(charge_limit.charge_snapshot(frame)['valid'])


class ChargeLimitCoordinatorTest(unittest.IsolatedAsyncioTestCase):
    def instance(self, frames=None):
        instance = object.__new__(coordinator.AitoDataCoordinator)
        instance.vehicles = [types.SimpleNamespace(id='vehicle', profile=types.SimpleNamespace(
            project_code='SERES-F3', platform_version='2'))]
        instance._prepare_car_lock = asyncio.Lock()
        instance.charge_limit_snapshots = {}
        instance.platform_versions = {'vehicle':'2'}
        instance.platform_versions_verified_at = datetime.now(TZ)
        instance.data = {'vehicle': {'retained': True}}
        instance.client = types.SimpleNamespace(control_charge_default=Mock())
        instance._async_dynamic_infos = AsyncMock(side_effect=frames or [raw_frame(),raw_frame(target=95,age=10)])
        instance._async_apig_request = AsyncMock()
        instance.async_set_updated_data = lambda value: setattr(instance, 'data', value)
        return instance

    async def apply(self, instance, **kwargs):
        values = dict(vehicle_id='vehicle', target=95, expected_previous=80, mode='ac',
            observed_at=(datetime.now(TZ)-timedelta(seconds=40)).isoformat())
        values.update(kwargs)
        with patch.object(coordinator, '_CHARGE_LIMIT_READBACK_DELAY_SECONDS', 0):
            return await instance.async_apply_charge_default(**values)

    async def test_one_command_requires_newer_matching_telemetry(self):
        before = raw_frame()
        old_match = {**before, 'charge': {**before['charge'], 'maxSocPercent':95}}
        instance = self.instance([before,old_match,raw_frame(target=95,age=10)])
        result = await self.apply(instance)
        self.assertEqual(result['state'], 'confirmed')
        self.assertTrue(result['vehicle_action_invoked'])
        self.assertEqual(instance._async_dynamic_infos.await_count, 3)
        instance._async_apig_request.assert_awaited_once_with(
            instance.client.control_charge_default,'vehicle',95,retry_after_refresh=False)
        self.assertTrue(instance.data['vehicle']['retained'])

    async def test_supported_model_platform_and_mode_gates(self):
        for project, platform in [('SERES-X1','2'),('SERES-F3','1'),('SERES-F3',None)]:
            instance = self.instance()
            instance.vehicles[0].profile.project_code = project
            instance.vehicles[0].profile.platform_version = platform
            instance.platform_versions['vehicle'] = platform
            self.assertEqual((await self.apply(instance))['state'],'unsupported_vehicle')
            instance._async_apig_request.assert_not_awaited()
        for kwargs in [dict(target=90),dict(mode='dc',target=95),dict(target=96),
                       dict(target=95.0),dict(expected_previous=80.0),dict(mode='unknown')]:
            instance = self.instance()
            with self.subTest(kwargs=kwargs), self.assertRaises(ValueError):
                await self.apply(instance, **kwargs)
            instance._async_apig_request.assert_not_awaited()

    async def test_ac_full_and_dc_defaults_use_only_the_matching_connector(self):
        for mode, target, ac, dc in [('ac',100,3,1),('dc',90,1,2)]:
            instance = self.instance([raw_frame(ac=ac,dc=dc),
                raw_frame(target=target,ac=ac,dc=dc,age=10)])
            result = await self.apply(instance, mode=mode,target=target)
            self.assertEqual(result['state'],'confirmed')
            instance._async_apig_request.assert_awaited_once_with(
                instance.client.control_charge_default,'vehicle',target,retry_after_refresh=False)

    async def test_manual_precondition_change_and_already_matching_are_noops(self):
        for target, previous, state in [(85,80,'manual_override'),(95,95,'confirmed')]:
            instance = self.instance([raw_frame(target=target)])
            result = await self.apply(instance, expected_previous=previous)
            self.assertEqual(result['state'],state)
            self.assertFalse(result['vehicle_action_invoked'])
            instance._async_apig_request.assert_not_awaited()

    async def test_stale_malformed_and_naive_preconditions_are_noops(self):
        for observed in [(datetime.now(TZ)-timedelta(seconds=121)).isoformat(),
                         'invalid','2026-09-29T00:00:00',None]:
            instance = self.instance()
            self.assertEqual((await self.apply(instance,observed_at=observed))['state'],'stale_precondition')
            instance._async_dynamic_infos.assert_not_awaited()
            instance._async_apig_request.assert_not_awaited()

    async def test_freshness_is_checked_after_waiting_for_command_lock(self):
        instance = self.instance()
        observed = datetime.now(TZ)
        await instance._prepare_car_lock.acquire()
        task = asyncio.create_task(self.apply(instance, observed_at=observed.isoformat()))
        await asyncio.sleep(0)
        class Later(datetime):
            @classmethod
            def now(cls, tz=None):
                return observed + timedelta(seconds=121)
        with patch.object(coordinator,'datetime',Later):
            instance._prepare_car_lock.release()
            result = await task
        self.assertEqual(result['state'],'stale_precondition')
        instance._async_dynamic_infos.assert_not_awaited()
        instance._async_apig_request.assert_not_awaited()

    async def test_invalid_connector_stale_source_and_read_failure_prevent_put(self):
        for frame in [raw_frame(age=121),raw_frame(ac=3,dc=2),raw_frame(ac=1,dc=1),
                      raw_frame(ac=1,dc=2),raw_frame(age=45),RuntimeError('synthetic')]:
            instance = self.instance([frame])
            self.assertEqual((await self.apply(instance))['state'],'invalid_precondition')
            instance._async_apig_request.assert_not_awaited()

    async def test_slow_preflight_cannot_extend_observation_lease(self):
        instance = self.instance()
        observed = datetime.now(TZ) - timedelta(seconds=40)
        class Later(datetime):
            @classmethod
            def now(cls, tz=None):
                return observed + timedelta(seconds=121)
        async def preflight(*_args):
            clock_patch.start()
            return raw_frame()
        clock_patch = patch.object(coordinator,'datetime',Later)
        instance._async_dynamic_infos.side_effect = preflight
        try:
            result = await self.apply(instance,observed_at=observed.isoformat())
        finally:
            clock_patch.stop()
        self.assertEqual(result['state'],'stale_precondition')
        instance._async_apig_request.assert_not_awaited()

    async def test_already_reached_while_charging_does_not_set_lower_limit(self):
        instance = self.instance([raw_frame(soc=96,status=6)])
        self.assertEqual((await self.apply(instance))['state'],'target_already_reached')
        instance._async_apig_request.assert_not_awaited()

    async def test_manual_value_during_readback_wins(self):
        instance = self.instance([raw_frame(),raw_frame(target=97,age=10)])
        result = await self.apply(instance)
        self.assertEqual(result['state'],'manual_override')
        self.assertEqual(result['actual_target'],97)
        self.assertEqual(instance._async_apig_request.await_count,1)

    async def test_failed_transport_and_failed_readback_are_uncertain_without_replay(self):
        for failure in ('put','readback','disconnected','timeout'):
            instance = self.instance()
            if failure == 'put':
                instance._async_apig_request.side_effect = TimeoutError('synthetic')
            elif failure == 'readback':
                instance._async_dynamic_infos.side_effect = [raw_frame(),RuntimeError('synthetic')]
            elif failure == 'disconnected':
                instance._async_dynamic_infos.side_effect = [raw_frame(),raw_frame(ac=1,dc=1)]
            else:
                instance._async_dynamic_infos.side_effect = [raw_frame(),raw_frame(age=10)]
            with patch.object(coordinator,'_CHARGE_LIMIT_READBACK_ATTEMPTS',1):
                result = await self.apply(instance)
            self.assertEqual(result['state'],'uncertain')
            self.assertTrue(result['vehicle_action_invoked'])
            self.assertEqual(instance._async_apig_request.await_count,1)

    async def test_auth_refresh_never_replays_the_mutating_request(self):
        instance = self.instance()
        instance.hass = types.SimpleNamespace(async_add_executor_job=AsyncMock(
            side_effect=api.AitoApiError(401, {'error':'synthetic token cancelled'})))
        instance._async_refresh_apig_authorization = AsyncMock()
        with patch.object(coordinator,'_is_cancelled_apig_token',return_value=True):
            with self.assertRaises(api.AitoApiError):
                await coordinator.AitoDataCoordinator._async_apig_request(instance,
                    instance.client.control_charge_default,'vehicle',95,retry_after_refresh=False)
        self.assertEqual(instance.hass.async_add_executor_job.await_count,1)
        instance._async_refresh_apig_authorization.assert_awaited_once()


class ChargeLimitServiceTest(unittest.IsolatedAsyncioTestCase):
    async def test_fixed_admin_service_response_schema_and_entity_route(self):
        import voluptuous as vol
        registrar = Mock()
        service = types.ModuleType('homeassistant.helpers.service')
        service.async_register_admin_service = registrar
        cv = types.ModuleType('homeassistant.helpers.config_validation')
        cv.entity_id = str
        helpers = types.ModuleType('homeassistant.helpers')
        helpers.__path__ = []
        helpers.config_validation = cv
        response_only = object()
        instance = types.SimpleNamespace(charge_limit_entities={'sensor.synthetic_charge_limit':'vehicle'},
            async_apply_charge_default=AsyncMock(return_value={'state':'confirmed'}))
        hass = types.SimpleNamespace(services=types.SimpleNamespace(has_service=lambda *_:False),
            data={'aito': {'entry':{'coordinator':instance}}})
        with patch.dict(sys.modules, {'homeassistant.helpers':helpers,
                'homeassistant.helpers.service':service,'homeassistant.helpers.config_validation':cv}), \
             patch.object(sys.modules['homeassistant.core'],'SupportsResponse',
                types.SimpleNamespace(ONLY=response_only),create=True), \
             patch.object(sys.modules['homeassistant.exceptions'],'HomeAssistantError',RuntimeError,create=True):
            charge_limit.register_service(hass)
            registrar.assert_called_once()
            args, kwargs = registrar.call_args
            self.assertEqual(args[:3],(hass,'aito','apply_charge_default'))
            self.assertIs(kwargs['supports_response'],response_only)
            schema = kwargs['schema']
            payload = dict(entity_id='sensor.synthetic_charge_limit',target=95,expected_previous=80,
                mode='ac',observed_at=datetime.now(TZ).isoformat())
            for changes in (dict(target=91),dict(target='95'),dict(vehicle_id='arbitrary'),
                            dict(mode='unknown'),dict(expected_previous=101)):
                with self.subTest(changes=changes), self.assertRaises(vol.Invalid):
                    schema({**payload,**changes})
            result = await args[3](types.SimpleNamespace(data=schema(payload)))
            self.assertEqual(result,{'state':'confirmed'})
            instance.async_apply_charge_default.assert_awaited_once_with(
                'vehicle',95,80,'ac',payload['observed_at'])
            with self.assertRaisesRegex(RuntimeError,'charge_limit_entity_unavailable'):
                await args[3](types.SimpleNamespace(data={**payload,'entity_id':'sensor.unknown'}))


if __name__ == '__main__':
    unittest.main()

class NoticeTelemetryTest(unittest.TestCase):
    def test_schedule_utc_to_shanghai_clock_and_no_private_fields(self):
        raw=raw_frame()
        raw['charge']['chargePlanList']=[{'startSwitch':1,'startTime':'1400','endTime':'2300','timeZone':'UTC','endSwitch':1,'vehicleId':'private','planId':123,'weeks':'1,2'}]
        value=charge_limit.charge_snapshot(raw)
        self.assertEqual(value['schedule']['start_clock'],'22:00')
        self.assertEqual(value['schedule']['end_clock'],'07:00')
        self.assertTrue(value['schedule']['timezone_confirmed'])
        self.assertNotIn('private',json.dumps(value))
        self.assertNotIn('weeks',value['schedule'])
    def test_unreported_or_unsupported_timezone_never_shifts_clock(self):
        for zone in (None, '', 'Europe/London', '+08:00', {'invalid':'object'}):
            with self.subTest(zone=zone):
                raw=raw_frame()
                raw['charge']['chargePlanList']=[{'startSwitch':1,'startTime':'0001','endTime':'0601','endSwitch':1,'timeZone':zone}]
                schedule=charge_limit.charge_snapshot(raw)['schedule']
                self.assertEqual(schedule['start_clock'],'00:01')
                self.assertEqual(schedule['end_clock'],'06:01')
                self.assertFalse(schedule['timezone_confirmed'])
    def test_explicit_timezone_contract(self):
        for zone, start, end in (('Asia/Shanghai','20:01','02:01'),
                                 ('GMT+08:00','20:01','02:01'),
                                 ('UTC','04:01','10:01'),
                                 ('GMT+00:00','04:01','10:01')):
            with self.subTest(zone=zone):
                raw=raw_frame()
                raw['charge']['chargePlanList']=[{'startSwitch':1,'startTime':'2001','endTime':'0201','endSwitch':1,'timeZone':zone}]
                schedule=charge_limit.charge_snapshot(raw)['schedule']
                self.assertEqual((schedule['start_clock'],schedule['end_clock']),(start,end))
                self.assertTrue(schedule['timezone_confirmed'])
    def test_missing_target_soc_still_has_connection_notice_but_no_write(self):
        raw=raw_frame();raw['charge']['maxSocPercent']=-1;raw['charge']['soc']=-1
        value=charge_limit.charge_snapshot(raw)
        self.assertFalse(value['valid']);self.assertTrue(value['notice_valid'])
        self.assertIsNone(value['target']);self.assertIsNone(value['soc'])
    def test_scheduled_values_not_retained_measurement(self):
        raw=raw_frame(status=25);raw['charge'].update(acChargeCurrent=32,chargeVoltage=220,remainChargeTime=100)
        value=charge_limit.charge_snapshot(raw)
        self.assertIsNone(value['current_a']);self.assertIsNone(value['remaining_minutes'])
    def test_charging_computes_current_voltage_and_rejects_sentinel(self):
        raw=raw_frame(status=6);raw['charge'].update(acChargeCurrent=-32,chargeVoltage=220,remainChargeTime=-1)
        value=charge_limit.charge_snapshot(raw)
        self.assertEqual(value['power_kw'],7.04)
        self.assertEqual(value['power_source'],'current_voltage_product')
        self.assertIsNone(value['remaining_minutes'])
