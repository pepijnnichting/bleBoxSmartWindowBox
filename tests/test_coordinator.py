"""Verify polling responds to actual device movement."""
import tempfile
import unittest
from datetime import timedelta
from types import SimpleNamespace
from unittest.mock import AsyncMock

from homeassistant.core import HomeAssistant
from homeassistant.helpers import frame
from homeassistant.helpers.update_coordinator import UpdateFailed

from custom_components.blebox_smartwindowbox.api import BleBoxApiError
from custom_components.blebox_smartwindowbox.coordinator import BleBoxCoordinator


class CoordinatorTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.config = tempfile.TemporaryDirectory()
        self.hass = HomeAssistant(self.config.name)
        frame.async_setup(self.hass)
        self.api = SimpleNamespace(window_extended_state=AsyncMock())
        self.coordinator = BleBoxCoordinator(self.hass, self.api)

    async def asyncTearDown(self):
        await self.hass.async_stop()
        self.config.cleanup()

    async def test_polling_accelerates_while_moving_and_returns_to_idle(self):
        self.assertEqual(self.coordinator.update_interval, timedelta(seconds=5))
        for state, seconds in [(1, 1), (0, 1), (2, 5), (3, 5), (4, 5)]:
            with self.subTest(state=state):
                payload = {'window': {'motors': [{'id': 0, 'state': state}]}}
                self.api.window_extended_state.return_value = payload
                await self.coordinator.async_refresh()
                self.assertEqual(self.coordinator.data, payload)
                self.assertEqual(self.coordinator.update_interval, timedelta(seconds=seconds))

    async def test_one_moving_motor_accelerates_polling(self):
        self.api.window_extended_state.return_value = {'window': {'motors': [
            {'id': 0, 'state': 2}, {'id': 1, 'state': 1},
        ]}}
        await self.coordinator.async_refresh()
        self.assertEqual(self.coordinator.update_interval, timedelta(seconds=1))

    async def test_transport_failure_is_update_failure(self):
        self.api.window_extended_state.side_effect = BleBoxApiError('unreachable')
        with self.assertRaises(UpdateFailed):
            await self.coordinator._async_update_data()
