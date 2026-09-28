"""Verified App metadata mapping; all requests use synthetic local mocks."""
import asyncio
from datetime import datetime, timedelta
import importlib
import types
import unittest
from unittest.mock import AsyncMock, Mock, patch

from test_prepare_car import coordinator

platform = importlib.import_module('custom_components.aito.charge_platform')
TZ = coordinator._SHANGHAI_TZ


def dictionary(values=None):
    return {'code':0, 'dicparamList':[{'dicItemId':platform.PLATFORM_DICTIONARY,
        'dictItemValueList': values if values is not None else [
            {'dicItemValue':'SERES-X1','dicItemValueName':'1'},
            {'dicItemValue':'SERES-F2','dicItemValueName':'2'}]}]}


class ChargePlatformParsingTest(unittest.TestCase):
    def test_verified_dictionary_applies_project_mapping_and_app_default(self):
        mapping = platform.parse_platform_dictionary(dictionary())
        self.assertEqual(platform.platform_for_project(mapping,'SERES-X1'),'1')
        self.assertEqual(platform.platform_for_project(mapping,'SERES-F2'),'2')
        self.assertEqual(platform.platform_for_project(mapping,'SERES-F3'),'2')
        self.assertIsNone(platform.platform_for_project(mapping,None))

    def test_missing_failed_empty_or_conflicting_dictionary_never_defaults(self):
        for response in [None,{}, {'code':1,'dicparamList':dictionary()['dicparamList']},
                {'code':0,'dicparamList':[]},dictionary([]),
                {'code':0,'dicparamList':[{'dicItemId':'other','dictItemValueList':[{}]}]},
                dictionary([{'dicItemValue':'SERES-X1','dicItemValueName':'1'},
                            {'dicItemValue':'SERES-X1','dicItemValueName':'2'}]),
                dictionary([{'dicItemValue':'SERES-X1','dicItemValueName':[]}])]:
            with self.subTest(response=response), self.assertRaises(ValueError):
                platform.parse_platform_dictionary(response)

    def test_observed_string_success_code_matches_native_json_get_int(self):
        response=dictionary();response['code']='0';response['resultCode']='0'
        mapping=platform.parse_platform_dictionary(response)
        self.assertEqual(platform.platform_for_project(mapping,'SERES-F3'),'2')
        for value in (False,0.0,'00','false','1',None):
            response['code']=value
            with self.subTest(code=value),self.assertRaises(ValueError):
                platform.parse_platform_dictionary(response)

    def test_cache_expiry_and_clock_skew_fail_closed(self):
        now = datetime.now(TZ)
        for stamp in [None,now.replace(tzinfo=None),now+timedelta(seconds=1),now-timedelta(hours=6)]:
            self.assertFalse(platform.platform_cache_fresh(stamp,now))
        self.assertTrue(platform.platform_cache_fresh(now-timedelta(hours=5,minutes=59),now))


class ChargePlatformCoordinatorTest(unittest.IsolatedAsyncioTestCase):
    def instance(self):
        instance = object.__new__(coordinator.AitoDataCoordinator)
        instance.vehicles = [types.SimpleNamespace(id='vehicle',profile=types.SimpleNamespace(
            project_code='SERES-F3',platform_version='2'))]
        instance.platform_versions = {}
        instance.platform_versions_verified_at = None
        instance._charge_platform_attempt_at = None
        instance._charge_platform_lock = asyncio.Lock()
        instance._prepare_car_lock = asyncio.Lock()
        instance.assets = {coordinator.CONF_XID:'synthetic-xid'}
        instance.client = types.SimpleNamespace(vehicle_dictionary_values=Mock(return_value=dictionary()))
        async def execute(fn):
            return fn()
        instance.hass = types.SimpleNamespace(async_add_executor_job=AsyncMock(side_effect=execute))
        instance._async_apig_request = AsyncMock()
        instance._async_dynamic_infos = AsyncMock()
        return instance

    async def test_fixed_query_populates_cache_without_mutating_assets(self):
        instance = self.instance()
        assets = dict(instance.assets)
        await instance._async_refresh_charge_platforms()
        instance.client.vehicle_dictionary_values.assert_called_once_with(
            ['DCC_VEHICLE_PLATFORM_VERSION'],xid='synthetic-xid')
        self.assertEqual(instance.charge_platform_version('vehicle'),'2')
        self.assertEqual(instance.assets,assets)
        await instance._async_refresh_charge_platforms()
        self.assertEqual(instance.client.vehicle_dictionary_values.call_count,1)

    async def test_failure_backoff_and_retry_do_not_adopt_profile_default(self):
        instance = self.instance()
        instance.client.vehicle_dictionary_values.side_effect = [RuntimeError('synthetic'),dictionary()]
        with patch.object(coordinator.time,'monotonic',return_value=100):
            await instance._async_refresh_charge_platforms()
        self.assertIsNone(instance.charge_platform_version('vehicle'))
        with patch.object(coordinator.time,'monotonic',return_value=399):
            await instance._async_refresh_charge_platforms()
        self.assertEqual(instance.client.vehicle_dictionary_values.call_count,1)
        with patch.object(coordinator.time,'monotonic',return_value=400):
            await instance._async_refresh_charge_platforms()
        self.assertEqual(instance.charge_platform_version('vehicle'),'2')

    async def test_missing_xid_and_bad_dictionary_never_enable_platform(self):
        instance = self.instance()
        instance.assets = {}
        await instance._async_refresh_charge_platforms()
        instance.hass.async_add_executor_job.assert_not_awaited()
        self.assertIsNone(instance.charge_platform_version('vehicle'))
        instance = self.instance()
        instance.client.vehicle_dictionary_values.return_value = dictionary([])
        await instance._async_refresh_charge_platforms()
        self.assertIsNone(instance.charge_platform_version('vehicle'))

    async def test_expired_cache_refreshes_and_failure_cannot_authorize_control(self):
        instance = self.instance()
        await instance._async_refresh_charge_platforms()
        instance.platform_versions_verified_at -= timedelta(hours=6)
        instance._charge_platform_attempt_at -= 6*3600
        self.assertIsNone(instance.charge_platform_version('vehicle'))
        instance.client.vehicle_dictionary_values.side_effect = RuntimeError('synthetic')
        await instance._async_refresh_charge_platforms()
        result = await instance.async_apply_charge_default('vehicle',95,80,'ac',datetime.now(TZ).isoformat())
        self.assertEqual(result['state'],'unsupported_vehicle')
        self.assertFalse(result['vehicle_action_invoked'])
        instance._async_dynamic_infos.assert_not_awaited()
        instance._async_apig_request.assert_not_awaited()
        self.assertEqual(instance.client.vehicle_dictionary_values.call_count,2)

    async def test_explicit_legacy_platform_blocks_even_with_profile_two(self):
        instance = self.instance()
        instance.client.vehicle_dictionary_values.return_value = dictionary([
            {'dicItemValue':'SERES-F3','dicItemValueName':'1'}])
        await instance._async_refresh_charge_platforms()
        self.assertEqual(instance.charge_platform_version('vehicle'),'1')
        result = await instance.async_apply_charge_default('vehicle',95,80,'ac',datetime.now(TZ).isoformat())
        self.assertEqual(result['state'],'unsupported_vehicle')
        instance._async_apig_request.assert_not_awaited()


if __name__ == '__main__':
    unittest.main()
