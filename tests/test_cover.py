"""Regression checks for upstream issues #1 and #2.

Run with: python -m unittest discover -s tests -v
Requires Home Assistant in the active Python environment.
"""
import asyncio
import unittest
from types import SimpleNamespace
from unittest.mock import AsyncMock, Mock, patch

from homeassistant.exceptions import HomeAssistantError
from homeassistant.helpers.update_coordinator import CoordinatorEntity

from custom_components.blebox_smartwindowbox.api import BleBoxApiError
from custom_components.blebox_smartwindowbox.cover import BleBoxMotorCover


class CoverTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        self.motor = {
            'id': 0, 'state': 2, 'currentPos': {'position': 40},
            'desiredPos': {'position': 80}, 'favPos': {'position': 30},
        }
        self.data = {'window': {'motors': [self.motor]}}
        self.api = SimpleNamespace(
            send_motor_command=AsyncMock(),
            window_extended_state=AsyncMock(return_value=self.data),
        )
        self.coordinator = SimpleNamespace(
            data=self.data, api=self.api, config_entry=None,
            async_request_refresh=AsyncMock(),
            async_refresh=AsyncMock(),
            async_set_updated_data=Mock(),
            async_set_update_error=Mock(),
        )
        self.cover = BleBoxMotorCover(self.coordinator, 'entry', 0, None)

    def test_reported_positions_and_closed_state(self):
        for raw, expected, closed in [(100, 0, True), (0, 100, False), (40, 60, False)]:
            with self.subTest(raw=raw):
                self.motor['currentPos']['position'] = raw
                self.assertEqual(self.cover.current_cover_position, expected)
                self.assertEqual(self.cover.extra_state_attributes['current_position'], expected)
                self.assertEqual(self.cover.is_closed, closed)
                self.assertEqual(self.cover.extra_state_attributes['device_position'], raw)

    def test_unknown_position(self):
        for value in (None, 'invalid'):
            self.motor['currentPos']['position'] = value
            self.assertIsNone(self.cover.current_cover_position)
            self.assertIsNone(self.cover.is_closed)
            self.assertIsNone(self.cover.extra_state_attributes['current_position'])

    def test_position_metadata_uses_home_assistant_scale(self):
        self.cover._move_target = 80
        self.cover._move_start_pos = 40
        attributes = self.cover.extra_state_attributes
        self.assertEqual(attributes['desired_position'], 20)
        self.assertEqual(attributes['favorite_position'], 70)
        self.assertEqual(attributes['move_target_position'], 20)
        self.assertEqual(attributes['move_start_position'], 60)

    async def test_endpoints_do_not_stop_or_skip_motor(self):
        # Cached/reported positions near or at an endpoint must still send
        # the native command, leaving physical completion to the device.
        for target, command in [(0, 'd'), (100, 'u')]:
            for raw in (0, 1, 50, 99, 100):
                with self.subTest(target=target, raw=raw):
                    self.api.send_motor_command.reset_mock()
                    self.motor['currentPos']['position'] = raw
                    await self.cover.async_set_cover_position(position=target)
                    self.api.send_motor_command.assert_awaited_once_with(0, command)
        self.api.window_extended_state.assert_not_awaited()

    async def test_intermediate_positions_move_in_correct_direction_and_stop(self):
        for target, initial, polled, command in [(70, 60, 30, 'u'), (20, 40, 80, 'd')]:
            with self.subTest(target=target):
                self.motor['currentPos']['position'] = initial
                self.api.send_motor_command.reset_mock()
                self.api.window_extended_state.return_value = {'window': {'motors': [
                    {'id': 0, 'currentPos': {'position': polled}}
                ]}}
                with patch('custom_components.blebox_smartwindowbox.cover.asyncio.sleep', new=AsyncMock()):
                    await self.cover.async_set_cover_position(position=target)
                self.assertEqual(
                    [call.args for call in self.api.send_motor_command.await_args_list],
                    [(0, command), (0, 's'), (0, 's')],
                )

    async def test_matching_intermediate_position_does_not_move(self):
        await self.cover.async_set_cover_position(position=60)
        self.api.send_motor_command.assert_not_awaited()

    async def test_invalid_target_does_not_move(self):
        for target in (None, 'invalid'):
            await self.cover.async_set_cover_position(position=target)
        self.api.send_motor_command.assert_not_awaited()

    async def test_commands_refresh_state_immediately(self):
        for method in (
            self.cover.async_open_cover, self.cover.async_close_cover,
            self.cover.async_stop_cover, self.cover.async_favorite,
            self.cover.async_next_step,
        ):
            with self.subTest(method=method.__name__):
                self.coordinator.async_refresh.reset_mock()
                await method()
                self.coordinator.async_refresh.assert_awaited_once()
        self.coordinator.async_request_refresh.assert_not_awaited()

    async def test_intermediate_motion_publishes_each_position(self):
        opening = {'window': {'motors': [
            {'id': 0, 'state': 1, 'currentPos': {'position': 35}}
        ]}}
        reached = {'window': {'motors': [
            {'id': 0, 'state': 1, 'currentPos': {'position': 30}}
        ]}}
        self.api.window_extended_state.side_effect = [opening, reached]
        with patch('custom_components.blebox_smartwindowbox.cover.asyncio.sleep', new=AsyncMock()):
            await self.cover.async_set_cover_position(position=70)
        self.assertEqual(
            [call.args[0] for call in self.coordinator.async_set_updated_data.call_args_list],
            [opening, reached],
        )
        self.assertEqual(self.coordinator.async_refresh.await_count, 2)

    def test_movement_state_and_position_agree(self):
        for state, raw, opening, closing in [(1, 30, True, False), (0, 70, False, True), (2, 50, False, False)]:
            with self.subTest(state=state):
                self.motor['state'] = state
                self.motor['currentPos']['position'] = raw
                self.assertEqual(self.cover.is_opening, opening)
                self.assertEqual(self.cover.is_closing, closing)
                self.assertEqual(self.cover.current_cover_position, 100 - raw)

    async def _start_blocked_controller(self):
        entered = asyncio.Event()
        async def read():
            entered.set()
            await asyncio.Event().wait()
        self.api.window_extended_state.side_effect = read
        caller = asyncio.create_task(self.cover.async_set_cover_position(position=70))
        await asyncio.wait_for(entered.wait(), 2)
        return caller

    async def test_each_new_command_cancels_old_controller(self):
        for method, command in [
            (self.cover.async_open_cover, 'u'), (self.cover.async_close_cover, 'd'),
            (self.cover.async_stop_cover, 's'), (self.cover.async_favorite, 'f'),
            (self.cover.async_next_step, 'n'),
        ]:
            with self.subTest(command=command), patch('custom_components.blebox_smartwindowbox.cover.POLL_INTERVAL_SEC', 0):
                self.api.send_motor_command.reset_mock()
                caller = await self._start_blocked_controller()
                await method()
                await asyncio.wait_for(caller, 2)
                self.assertEqual([c.args[1] for c in self.api.send_motor_command.await_args_list], ['u', command])
                self.assertIsNone(self.cover._position_task)

    async def test_new_position_replaces_old_controller(self):
        with patch('custom_components.blebox_smartwindowbox.cover.POLL_INTERVAL_SEC', 0), patch('custom_components.blebox_smartwindowbox.cover.EXTRA_STOP_DELAY_SEC', 0):
            caller = await self._start_blocked_controller()
            self.api.window_extended_state.side_effect = None
            self.api.window_extended_state.return_value = {'window': {'motors': [
                {'id': 0, 'currentPos': {'position': 80}}
            ]}}
            await self.cover.async_set_cover_position(position=20)
            await asyncio.wait_for(caller, 2)
        self.assertEqual([c.args[1] for c in self.api.send_motor_command.await_args_list], ['u', 'd', 's', 's'])

    async def test_replacement_at_current_position_stops_previous_movement(self):
        with patch('custom_components.blebox_smartwindowbox.cover.POLL_INTERVAL_SEC', 0):
            caller = await self._start_blocked_controller()
            await self.cover.async_set_cover_position(position=60)
            await asyncio.wait_for(caller, 2)
        self.assertEqual([c.args[1] for c in self.api.send_motor_command.await_args_list], ['u', 's'])

    async def test_new_command_interrupts_extra_stop_delay(self):
        delayed = asyncio.Event()
        async def sleep(delay):
            if delay > 0:
                delayed.set()
                await asyncio.Event().wait()
        self.api.window_extended_state.return_value = {'window': {'motors': [
            {'id': 0, 'currentPos': {'position': 30}}
        ]}}
        with patch('custom_components.blebox_smartwindowbox.cover.POLL_INTERVAL_SEC', 0), patch('custom_components.blebox_smartwindowbox.cover.asyncio.sleep', sleep):
            caller = asyncio.create_task(self.cover.async_set_cover_position(position=70))
            await asyncio.wait_for(delayed.wait(), 2)
            await self.cover.async_close_cover()
            await asyncio.wait_for(caller, 2)
        self.assertEqual([c.args[1] for c in self.api.send_motor_command.await_args_list], ['u', 's', 'd'])

    async def test_caller_cancellation_stops_and_cleans_up(self):
        with patch('custom_components.blebox_smartwindowbox.cover.POLL_INTERVAL_SEC', 0):
            caller = await self._start_blocked_controller()
            caller.cancel()
            with self.assertRaises(asyncio.CancelledError):
                await caller
        self.assertEqual([c.args[1] for c in self.api.send_motor_command.await_args_list], ['u', 's'])
        self.assertIsNone(self.cover._position_task)

    async def test_unload_stops_and_cancels_controller(self):
        with patch('custom_components.blebox_smartwindowbox.cover.POLL_INTERVAL_SEC', 0), patch.object(CoordinatorEntity, 'async_will_remove_from_hass', new=AsyncMock()) as remove:
            caller = await self._start_blocked_controller()
            await self.cover.async_will_remove_from_hass()
            await asyncio.wait_for(caller, 2)
            remove.assert_awaited_once()
        self.assertEqual([c.args[1] for c in self.api.send_motor_command.await_args_list], ['u', 's'])

    async def test_poll_failure_stops_and_reports_original_error(self):
        self.api.window_extended_state.side_effect = BleBoxApiError('poll failed')
        with patch('custom_components.blebox_smartwindowbox.cover.POLL_INTERVAL_SEC', 0):
            with self.assertRaisesRegex(HomeAssistantError, 'poll failed'):
                await self.cover.async_set_cover_position(position=70)
        self.assertEqual([c.args[1] for c in self.api.send_motor_command.await_args_list], ['u', 's'])
        self.coordinator.async_set_update_error.assert_called_once()
        self.assertIsNone(self.cover._position_task)

    async def test_failed_recovery_stop_preserves_original_error(self):
        self.api.window_extended_state.side_effect = BleBoxApiError('poll failed')
        self.api.send_motor_command.side_effect = [None, BleBoxApiError('stop failed')]
        with patch('custom_components.blebox_smartwindowbox.cover.POLL_INTERVAL_SEC', 0), self.assertLogs('custom_components.blebox_smartwindowbox.cover', level='WARNING'):
            with self.assertRaisesRegex(HomeAssistantError, 'poll failed'):
                await self.cover.async_set_cover_position(position=70)
        self.assertIsNone(self.cover._position_task)

    async def test_timeout_stops_and_reports_failure(self):
        task = asyncio.create_task(self.cover._async_move_to_position(30, 'opening', 0))
        self.cover._position_task = task
        with self.assertRaisesRegex(HomeAssistantError, 'timed out'):
            await task
        self.assertEqual([c.args[1] for c in self.api.send_motor_command.await_args_list], ['u', 's'])
        self.assertIsNone(self.cover._position_task)

    async def test_native_command_error_is_reported(self):
        self.api.send_motor_command.side_effect = BleBoxApiError('offline')
        with self.assertRaisesRegex(HomeAssistantError, 'offline'):
            await self.cover.async_close_cover()
        self.coordinator.async_set_update_error.assert_called_once()

    async def test_failed_replacement_attempts_stop_of_previous_movement(self):
        with patch('custom_components.blebox_smartwindowbox.cover.POLL_INTERVAL_SEC', 0):
            caller = await self._start_blocked_controller()
            self.api.send_motor_command.side_effect = [BleBoxApiError('replacement failed'), None]
            with self.assertRaisesRegex(HomeAssistantError, 'replacement failed'):
                await self.cover.async_close_cover()
            await asyncio.wait_for(caller, 2)
        self.assertEqual([c.args[1] for c in self.api.send_motor_command.await_args_list], ['u', 'd', 's'])
        self.assertIsNone(self.cover._position_task)

    def test_reversal_and_retarget_reset_progress_and_travel_time(self):
        self.motor['calibrationParameters'] = {'maxMoveTimeUpMs': 20000, 'maxMoveTimeDownMs': 40000}
        self.motor.update(state=1, currentPos={'position': 80}, desiredPos={'position': 0})
        with patch.object(self.cover, 'async_write_ha_state'), patch('custom_components.blebox_smartwindowbox.cover.time.monotonic', return_value=100) as clock:
            self.cover._handle_coordinator_update()
            self.motor['currentPos']['position'] = 60
            clock.return_value = 105
            self.cover._handle_coordinator_update()
            self.assertEqual(self.cover.extra_state_attributes['move_progress_pct'], 25)
            self.motor.update(state=0, desiredPos={'position': 100})
            clock.return_value = 110
            self.cover._handle_coordinator_update()
            attrs = self.cover.extra_state_attributes
            self.assertEqual(attrs['move_direction'], 'closing')
            self.assertEqual(attrs['move_start_position'], 40)
            self.assertEqual(attrs['move_elapsed_s'], 0)
            self.assertEqual(attrs['move_progress_pct'], 0)
            self.assertEqual(attrs['estimated_remaining_s'], 16)
            self.assertEqual(attrs['estimated_total_s'], 40)
            self.motor.update(currentPos={'position': 70}, desiredPos={'position': 90})
            clock.return_value = 115
            self.cover._handle_coordinator_update()
            self.assertEqual(self.cover.extra_state_attributes['move_start_position'], 30)
            self.assertEqual(self.cover.extra_state_attributes['move_target_position'], 10)
            self.assertEqual(self.cover.extra_state_attributes['move_progress_pct'], 0)
            self.motor['state'] = 2
            self.cover._handle_coordinator_update()
            self.assertFalse(self.cover.extra_state_attributes['moving'])
            self.assertIsNone(self.cover.extra_state_attributes['move_elapsed_s'])
