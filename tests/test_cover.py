"""Regression checks for upstream issues #1 and #2.

Run with: python -m unittest discover -s tests -v
Requires Home Assistant in the active Python environment.
"""
import unittest
from types import SimpleNamespace
from unittest.mock import AsyncMock, Mock, patch

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
