from __future__ import annotations

import asyncio
import logging
import time
from typing import Any

import voluptuous as vol

from homeassistant.components.cover import CoverEntity, CoverEntityFeature
from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant
from homeassistant.exceptions import HomeAssistantError
from homeassistant.helpers import entity_platform
from homeassistant.helpers.entity_platform import AddEntitiesCallback
from homeassistant.helpers.update_coordinator import CoordinatorEntity, UpdateFailed

from .api import BleBoxApiError
from .const import (
    DOMAIN,
    CONF_NAME,
    CMD_UP,
    CMD_DOWN,
    CMD_STOP,
    CMD_FAV,
    CMD_NEXT,
    POSITION_TOLERANCE,
    POLL_INTERVAL_SEC,
    EXTRA_STOP_DELAY_SEC,
)
from .coordinator import BleBoxCoordinator

_LOGGER = logging.getLogger(__name__)


def _motor_list(data: dict[str, Any]) -> list[dict[str, Any]]:
    return (data.get("window") or {}).get("motors") or []


def _ha_position(device_position: Any) -> int | None:
    """Convert BleBox's closed percentage to Home Assistant's open percentage."""
    try:
        return 100 - int(device_position)
    except (TypeError, ValueError):
        return None


async def async_setup_entry(
    hass: HomeAssistant, entry: ConfigEntry, async_add_entities: AddEntitiesCallback
) -> None:
    coordinator: BleBoxCoordinator = hass.data[DOMAIN][entry.entry_id]["coordinator"]
    motors = _motor_list(coordinator.data)

    name_prefix = entry.data.get(CONF_NAME) or None

    entities: list[BleBoxMotorCover] = []
    for motor in motors:
        channel = int(motor.get("id", 0))
        entities.append(BleBoxMotorCover(coordinator, entry.entry_id, channel, name_prefix))

    async_add_entities(entities)

    # Proper entity services (UI entity selector included)
    platform = entity_platform.async_get_current_platform()
    platform.async_register_entity_service("favorite", {}, "async_favorite")
    platform.async_register_entity_service("next_step", {}, "async_next_step")
    platform.async_register_entity_service(
        "set_position",
        {vol.Required("position"): vol.Coerce(int)},
        "async_set_cover_position",
    )


class BleBoxMotorCover(CoordinatorEntity[BleBoxCoordinator], CoverEntity):
    _attr_has_entity_name = True

    def __init__(self, coordinator: BleBoxCoordinator, entry_id: str, channel: int, name_prefix: str | None) -> None:
        super().__init__(coordinator)
        self._entry_id = entry_id
        self._channel = channel
        self._name_prefix = name_prefix

        self._attr_unique_id = f"{entry_id}_motor_{channel}"
        self._attr_supported_features = (
            CoverEntityFeature.OPEN
            | CoverEntityFeature.CLOSE
            | CoverEntityFeature.STOP
            | CoverEntityFeature.SET_POSITION
        )

        # Movement tracking for ANY movement (open/close/favorite/manual)
        self._tracked_moving: bool = False
        self._move_started: float | None = None
        self._move_start_pos: int | None = None
        self._move_target: int | None = None
        self._move_direction: str | None = None  # "opening" / "closing"
        self._move_full_time_s: float | None = None
        self._command_lock = asyncio.Lock()
        self._position_task: asyncio.Task[None] | None = None

    def _motor(self) -> dict[str, Any] | None:
        for m in _motor_list(self.coordinator.data):
            if int(m.get("id", -1)) == self._channel:
                return m
        return None

    @property
    def name(self) -> str | None:
        motor = self._motor() or {}
        base = motor.get("name") or f"Motor {self._channel}"
        if self._name_prefix:
            return f"{self._name_prefix} {base}"
        return str(base)

    # Home Assistant: 0 = fully closed, 100 = fully open.
    @property
    def current_cover_position(self) -> int | None:
        motor = self._motor()
        if not motor:
            return None
        pos = (motor.get("currentPos") or {}).get("position")
        return _ha_position(pos)

    # Your observed mapping:
    # 0 closing (moving to 100)
    # 1 opening (moving to 0)
    # 2 idle at intermediate position (e.g. favorite)
    # 3 idle at closed (100)
    # 4 idle at open (0)
    @property
    def is_opening(self) -> bool:
        motor = self._motor() or {}
        return motor.get("state") == 1

    @property
    def is_closing(self) -> bool:
        motor = self._motor() or {}
        return motor.get("state") == 0

    @property
    def is_closed(self) -> bool | None:
        pos = self.current_cover_position
        if pos is None:
            return None
        return pos <= 0

    def _full_travel_time_s(self, motor: dict[str, Any], direction: str) -> float:
        calib = motor.get("calibrationParameters") or {}
        up_ms = calib.get("maxMoveTimeUpMs") or 40000
        down_ms = calib.get("maxMoveTimeDownMs") or 40000
        ms = up_ms if direction == "opening" else down_ms
        try:
            return max(1.0, float(ms) / 1000.0)
        except (TypeError, ValueError):
            return 40.0

    def _handle_coordinator_update(self) -> None:
        motor = self._motor() or {}

        state = motor.get("state")
        cur = (motor.get("currentPos") or {}).get("position")
        des = (motor.get("desiredPos") or {}).get("position")

        try:
            cur_i = int(cur) if cur is not None else None
        except (TypeError, ValueError):
            cur_i = None
        try:
            des_i = int(des) if des is not None else None
        except (TypeError, ValueError):
            des_i = None

        moving_now = state in (0, 1)

        if moving_now:
            direction = "opening" if state == 1 else "closing"
            target = des_i

            if (
                not self._tracked_moving
                or direction != self._move_direction
                or target != self._move_target
            ):
                self._tracked_moving = True
                self._move_started = time.monotonic()
                self._move_start_pos = cur_i
                self._move_direction = direction
                self._move_target = target
                self._move_full_time_s = self._full_travel_time_s(motor, direction)
            elif self._move_full_time_s is None:
                self._move_full_time_s = self._full_travel_time_s(motor, direction)

        else:
            if self._tracked_moving:
                self._tracked_moving = False
                self._move_started = None
                self._move_start_pos = None
                self._move_target = None
                self._move_direction = None
                self._move_full_time_s = None

        super()._handle_coordinator_update()

    @property
    def extra_state_attributes(self) -> dict[str, Any]:
        motor = self._motor() or {}
        calib = motor.get("calibrationParameters") or {}
        fav = motor.get("favPos") or {}

        cur = (motor.get("currentPos") or {}).get("position")
        des = (motor.get("desiredPos") or {}).get("position")

        try:
            cur_i = int(cur) if cur is not None else None
        except (TypeError, ValueError):
            cur_i = None
        try:
            des_i = int(des) if des is not None else None
        except (TypeError, ValueError):
            des_i = None

        estimated_total_s = None
        estimated_remaining_s = None
        move_elapsed_s = None
        move_progress_pct = None

        if self._tracked_moving and self._move_started is not None and self._move_full_time_s is not None:
            move_elapsed_s = round(time.monotonic() - self._move_started, 2)

            if cur_i is not None and self._move_target is not None:
                distance_pct = abs(cur_i - self._move_target) / 100.0
                estimated_total_s = round(self._move_full_time_s, 2)
                estimated_remaining_s = round(distance_pct * self._move_full_time_s, 2)

            if cur_i is not None and self._move_target is not None and self._move_start_pos is not None:
                total_dist = abs(self._move_start_pos - self._move_target)
                remaining_dist = abs(cur_i - self._move_target)
                if total_dist == 0:
                    move_progress_pct = 100
                else:
                    progress = (total_dist - remaining_dist) / total_dist * 100.0
                    move_progress_pct = int(max(0.0, min(100.0, round(progress))))

        at_fav = None
        if cur_i is not None and fav.get("position") is not None:
            try:
                at_fav = int(cur_i) == int(fav.get("position"))
            except (TypeError, ValueError):
                at_fav = None

        return {
            "enabled": motor.get("enabled"),
            "motor_state": motor.get("state"),
            "current_position": _ha_position(cur_i),
            "device_position": cur_i,
            "desired_position": _ha_position(des_i),
            "favorite_position": _ha_position(fav.get("position")),
            "at_favorite": at_fav,
            "control_type": motor.get("controlType"),
            "icon_set": motor.get("iconSet"),
            "is_calibrated": calib.get("isCalibrated"),
            "max_move_time_up_ms": calib.get("maxMoveTimeUpMs"),
            "max_move_time_down_ms": calib.get("maxMoveTimeDownMs"),
            # movement metadata (any movement)
            "moving": self._tracked_moving,
            "move_direction": self._move_direction,
            "move_target_position": _ha_position(self._move_target),
            "move_start_position": _ha_position(self._move_start_pos),
            "move_elapsed_s": move_elapsed_s,
            "estimated_total_s": estimated_total_s,
            "estimated_remaining_s": estimated_remaining_s,
            "move_progress_pct": move_progress_pct,
        }

    async def _async_cancel_position_task(self, *, stop: bool = False) -> bool:
        """Cancel the previous controller while holding the command lock."""
        task = self._position_task
        self._position_task = None
        if task is None or task.done():
            return False
        _LOGGER.debug(
            "Motor %s: cancelling previous position controller (stop=%s)",
            self._channel, stop,
        )
        task.cancel()
        await asyncio.gather(task, return_exceptions=True)
        if stop:
            await self._async_best_effort_stop()
        return True

    async def _async_best_effort_stop(self) -> None:
        """Try stopping once, keeping the original error if the device is offline."""
        _LOGGER.debug("Motor %s: recovery/cancellation stop", self._channel)
        try:
            await self.coordinator.api.send_motor_command(self._channel, CMD_STOP)
        except BleBoxApiError:
            _LOGGER.warning("Could not stop BleBox motor %s", self._channel, exc_info=True)

    async def _async_command(self, command: str) -> None:
        """Replace any position controller before sending a new command."""
        async with self._command_lock:
            replaced = await self._async_cancel_position_task()
            _LOGGER.debug("Motor %s: native command %s", self._channel, command)
            try:
                await self.coordinator.api.send_motor_command(self._channel, command)
            except BleBoxApiError as err:
                self.coordinator.async_set_update_error(UpdateFailed(str(err)))
                if replaced:
                    await self._async_best_effort_stop()
                raise HomeAssistantError(f"BleBox command failed: {err}") from err
            await self.coordinator.async_refresh()

    async def async_open_cover(self, **kwargs: Any) -> None:
        await self._async_command(CMD_UP)

    async def async_close_cover(self, **kwargs: Any) -> None:
        await self._async_command(CMD_DOWN)

    async def async_stop_cover(self, **kwargs: Any) -> None:
        await self._async_command(CMD_STOP)

    async def async_favorite(self) -> None:
        await self._async_command(CMD_FAV)

    async def async_next_step(self) -> None:
        await self._async_command(CMD_NEXT)

    async def async_will_remove_from_hass(self) -> None:
        _LOGGER.debug("Motor %s: removing entity", self._channel)
        async with self._command_lock:
            await self._async_cancel_position_task(stop=True)
        await super().async_will_remove_from_hass()

    async def async_set_cover_position(self, **kwargs: Any) -> None:
        """Use native endpoints and an interruptible controller in between."""
        try:
            target = int(kwargs.get("position"))
        except (TypeError, ValueError):
            return
        target = max(0, min(100, target))
        _LOGGER.debug(
            "Motor %s: requested HA position %s (current=%s, motor_state=%s)",
            self._channel, target, self.current_cover_position,
            (self._motor() or {}).get("state"),
        )

        # Let the device finish calibrated endpoint travel without early stops.
        if target == 0:
            await self.async_close_cover()
            return
        if target == 100:
            await self.async_open_cover()
            return

        target = 100 - target
        async with self._command_lock:
            replaced = await self._async_cancel_position_task()
            motor = self._motor()
            if not motor:
                return
            try:
                cur_i = int((motor.get("currentPos") or {}).get("position"))
            except (TypeError, ValueError):
                cur_i = None

            if cur_i is not None and abs(cur_i - target) <= POSITION_TOLERANCE:
                if replaced or motor.get("state") in (0, 1):
                    await self._async_command_stop_at_target()
                return

            direction = "opening" if cur_i is None or target < cur_i else "closing"
            timeout_s = max(5.0, self._full_travel_time_s(motor, direction) + 5.0)
            task = asyncio.create_task(
                self._async_move_to_position(target, direction, timeout_s)
            )
            self._position_task = task

        try:
            # Caller cancellation is handled under the command lock below.
            await asyncio.shield(task)
        except asyncio.CancelledError:
            if asyncio.current_task().cancelling():
                _LOGGER.debug("Motor %s: position service cancelled", self._channel)
                async with self._command_lock:
                    if self._position_task is task:
                        await self._async_cancel_position_task(stop=True)
                raise
            # A newer command superseded this controller successfully.

    async def _async_command_stop_at_target(self) -> None:
        _LOGGER.debug("Motor %s: already at requested position; stopping", self._channel)
        try:
            await self.coordinator.api.send_motor_command(self._channel, CMD_STOP)
        except BleBoxApiError as err:
            self.coordinator.async_set_update_error(UpdateFailed(str(err)))
            raise HomeAssistantError(f"BleBox stop failed: {err}") from err
        await self.coordinator.async_refresh()

    async def _async_move_to_position(
        self, target: int, direction: str, timeout_s: float
    ) -> None:
        """Publish readings and stop at the target until replaced or cancelled."""
        try:
            # Include transport and refresh time in the movement deadline.
            start = time.monotonic()
            command = CMD_UP if direction == "opening" else CMD_DOWN
            _LOGGER.debug(
                "Motor %s: position controller %s to HA position %s (timeout=%ss)",
                self._channel, direction, 100 - target, timeout_s,
            )
            await self.coordinator.api.send_motor_command(self._channel, command)
            await self.coordinator.async_refresh()

            while True:
                if time.monotonic() - start > timeout_s:
                    raise HomeAssistantError("BleBox position movement timed out")
                await asyncio.sleep(POLL_INTERVAL_SEC)
                data = await self.coordinator.api.window_extended_state()
                self.coordinator.async_set_updated_data(data)
                motor = next(
                    (m for m in _motor_list(data) if int(m.get("id", -1)) == self._channel),
                    None,
                )
                if not motor:
                    continue
                try:
                    position = int((motor.get("currentPos") or {}).get("position"))
                except (TypeError, ValueError):
                    continue

                reached = (
                    position <= target + POSITION_TOLERANCE
                    if direction == "opening"
                    else position >= target - POSITION_TOLERANCE
                )
                if reached:
                    _LOGGER.debug(
                        "Motor %s: reached HA position %s (target=%s); stopping",
                        self._channel, 100 - position, 100 - target,
                    )
                    await self.coordinator.api.send_motor_command(self._channel, CMD_STOP)
                    await asyncio.sleep(EXTRA_STOP_DELAY_SEC)
                    await self.coordinator.api.send_motor_command(self._channel, CMD_STOP)
                    await self.coordinator.async_refresh()
                    return
        except (BleBoxApiError, HomeAssistantError) as err:
            _LOGGER.debug("Motor %s: position controller failed: %s", self._channel, err)
            async with self._command_lock:
                if self._position_task is asyncio.current_task():
                    if isinstance(err, BleBoxApiError):
                        self.coordinator.async_set_update_error(UpdateFailed(str(err)))
                    await self._async_best_effort_stop()
            raise HomeAssistantError(f"BleBox position control failed: {err}") from err
        finally:
            if self._position_task is asyncio.current_task():
                self._position_task = None
