from __future__ import annotations

import asyncio
import time
from typing import Any

import voluptuous as vol

from homeassistant.components.cover import CoverEntity, CoverEntityFeature
from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant
from homeassistant.helpers import entity_platform
from homeassistant.helpers.entity_platform import AddEntitiesCallback
from homeassistant.helpers.update_coordinator import CoordinatorEntity

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

            if not self._tracked_moving:
                self._tracked_moving = True
                self._move_started = time.monotonic()
                self._move_start_pos = cur_i
                self._move_direction = direction
                self._move_target = target
                self._move_full_time_s = self._full_travel_time_s(motor, direction)
            else:
                # update target/direction if changed mid-move
                self._move_direction = direction
                self._move_target = target
                if self._move_full_time_s is None:
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

    async def async_open_cover(self, **kwargs: Any) -> None:
        await self.coordinator.api.send_motor_command(self._channel, CMD_UP)
        await self.coordinator.async_request_refresh()

    async def async_close_cover(self, **kwargs: Any) -> None:
        await self.coordinator.api.send_motor_command(self._channel, CMD_DOWN)
        await self.coordinator.async_request_refresh()

    async def async_stop_cover(self, **kwargs: Any) -> None:
        await self.coordinator.api.send_motor_command(self._channel, CMD_STOP)
        await self.coordinator.async_request_refresh()

    async def async_favorite(self) -> None:
        await self.coordinator.api.send_motor_command(self._channel, CMD_FAV)
        await self.coordinator.async_request_refresh()

    async def async_next_step(self) -> None:
        await self.coordinator.api.send_motor_command(self._channel, CMD_NEXT)
        await self.coordinator.async_request_refresh()

    async def async_set_cover_position(self, **kwargs: Any) -> None:
        """
        Emulate set position by moving and stopping when target reached.
        Position scale: 0=closed, 100=open.
        Endpoints use native commands so the device can finish its travel.
        """
        target = kwargs.get("position")
        if target is None:
            return

        try:
            target = int(target)
        except (TypeError, ValueError):
            return
        target = max(0, min(100, target))

        # A reported endpoint may precede the physical end of travel.
        # Do not stop the motor or skip the command based on cached position.
        if target == 0:
            await self.async_close_cover()
            return
        if target == 100:
            await self.async_open_cover()
            return

        # Keep the polling loop in the native BleBox coordinate system.
        target = 100 - target
        tol = POSITION_TOLERANCE

        motor = self._motor()
        if not motor:
            return

        cur = (motor.get("currentPos") or {}).get("position")
        try:
            cur_i = int(cur)
        except (TypeError, ValueError):
            cur_i = None

        if cur_i is not None and abs(cur_i - target) <= POSITION_TOLERANCE:
            return

        direction = "opening" if (cur_i is None or target < cur_i) else "closing"
        command = CMD_UP if direction == "opening" else CMD_DOWN

        # Timeout based on calibration full travel time + buffer
        full_time_s = self._full_travel_time_s(motor, direction)
        timeout_s = max(5.0, full_time_s + 5.0)

        await self.coordinator.api.send_motor_command(self._channel, command)

        start = time.monotonic()
        while True:
            if time.monotonic() - start > timeout_s:
                await self.coordinator.api.send_motor_command(self._channel, CMD_STOP)
                await self.coordinator.async_request_refresh()
                return

            await asyncio.sleep(POLL_INTERVAL_SEC)

            # Poll direct for freshest position
            data = await self.coordinator.api.window_extended_state()
            motors = (data.get("window") or {}).get("motors") or []
            m = next((mm for mm in motors if int(mm.get("id", -1)) == self._channel), None)
            if not m:
                continue

            pos = (m.get("currentPos") or {}).get("position")
            try:
                pos_i = int(pos)
            except (TypeError, ValueError):
                continue

            if direction == "opening":
                # moving downward (toward 0): stop once we've reached/passed target
                if pos_i <= target + tol:
                    await self.coordinator.api.send_motor_command(self._channel, CMD_STOP)
                    await asyncio.sleep(EXTRA_STOP_DELAY_SEC)
                    await self.coordinator.api.send_motor_command(self._channel, CMD_STOP)
                    await self.coordinator.async_request_refresh()
                    return
            else:
                # closing upward (toward 100): stop once we've reached/passed target
                if pos_i >= target - tol:
                    await self.coordinator.api.send_motor_command(self._channel, CMD_STOP)
                    await asyncio.sleep(EXTRA_STOP_DELAY_SEC)
                    await self.coordinator.api.send_motor_command(self._channel, CMD_STOP)
                    await self.coordinator.async_request_refresh()
                    return
