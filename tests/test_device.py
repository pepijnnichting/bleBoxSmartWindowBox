"""Check shared device metadata and setup failures."""
import tempfile
import unittest
from types import SimpleNamespace
from unittest.mock import AsyncMock

from homeassistant.core import HomeAssistant
from homeassistant.helpers import frame
from homeassistant.helpers.update_coordinator import UpdateFailed

from custom_components.blebox_smartwindowbox.api import BleBoxApiError, DeviceInfo
from custom_components.blebox_smartwindowbox.binary_sensor import BleBoxRainBinarySensor
from custom_components.blebox_smartwindowbox.coordinator import BleBoxCoordinator
from custom_components.blebox_smartwindowbox.cover import BleBoxMotorCover


class DeviceTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.config = tempfile.TemporaryDirectory()
        self.hass = HomeAssistant(self.config.name)
        frame.async_setup(self.hass)
        self.info = DeviceInfo('Bedroom window', 'smartWindowBox', '20180604', '1.0', '2.0', 'device-123', None)
        self.api = SimpleNamespace(
            device_state=AsyncMock(return_value=self.info),
            base_url='http://blebox.local',
        )
        self.coordinator = BleBoxCoordinator(self.hass, self.api)

    async def asyncTearDown(self):
        await self.hass.async_stop()
        self.config.cleanup()

    async def test_entities_share_stable_device_identifier_and_metadata(self):
        await self.coordinator._async_setup()
        cover = BleBoxMotorCover(self.coordinator, 'existing-entry', 0, None)
        sensor = BleBoxRainBinarySensor(self.coordinator, 'existing-entry', 0)
        self.assertEqual(cover.device_class, 'window')
        self.assertEqual(sensor.device_class, 'moisture')
        self.assertEqual(cover.device_info, sensor.device_info)
        self.assertEqual(cover.device_info['identifiers'], {('blebox_smartwindowbox', 'device-123')})
        self.assertEqual(cover.device_info['manufacturer'], 'BleBox')
        self.assertEqual(cover.device_info['name'], 'Bedroom window')
        self.assertEqual(cover.device_info['model'], 'smartWindowBox')
        self.assertEqual(cover.device_info['hw_version'], '1.0')
        self.assertEqual(cover.device_info['sw_version'], '2.0')
        self.assertEqual(cover.device_info['configuration_url'], 'http://blebox.local')
        # Preserve entity identifiers already stored by existing installations.
        self.assertEqual(cover.unique_id, 'existing-entry_motor_0')
        self.assertEqual(sensor.unique_id, 'existing-entry_rain_0')

    async def test_metadata_uses_defaults_when_optional_fields_are_missing(self):
        self.info.device_name = ''
        self.info.device_type = ''
        self.info.hw = None
        self.info.fw = None
        await self.coordinator._async_setup()
        self.assertEqual(self.coordinator.device_info['name'], 'BleBox smartWindowBox')
        self.assertEqual(self.coordinator.device_info['model'], 'smartWindowBox')

    async def test_missing_device_identifier_fails_setup(self):
        self.info.device_id = ''
        with self.assertRaisesRegex(UpdateFailed, 'no device id'):
            await self.coordinator._async_setup()

    async def test_metadata_connection_failure_is_retriable_update_failure(self):
        self.api.device_state.side_effect = BleBoxApiError('offline')
        with self.assertRaisesRegex(UpdateFailed, 'offline'):
            await self.coordinator._async_setup()
