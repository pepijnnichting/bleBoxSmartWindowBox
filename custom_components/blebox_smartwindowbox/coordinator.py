from __future__ import annotations

from datetime import timedelta
from typing import Any

from homeassistant.core import HomeAssistant
from homeassistant.helpers.update_coordinator import DataUpdateCoordinator, UpdateFailed

from .api import BleBoxSmartWindowBoxApi, BleBoxApiError
from .const import DOMAIN, MOVING_UPDATE_INTERVAL_SECONDS, UPDATE_INTERVAL_SECONDS


class BleBoxCoordinator(DataUpdateCoordinator[dict[str, Any]]):
    def __init__(self, hass: HomeAssistant, api: BleBoxSmartWindowBoxApi) -> None:
        super().__init__(
            hass,
            logger=__import__("logging").getLogger(__name__),
            name=DOMAIN,
            update_interval=timedelta(seconds=UPDATE_INTERVAL_SECONDS),
        )
        self.api = api

    async def _async_update_data(self) -> dict[str, Any]:
        try:
            data = await self.api.window_extended_state()
        except BleBoxApiError as e:
            raise UpdateFailed(str(e)) from e

        motors = (data.get("window") or {}).get("motors") or []
        moving = any(motor.get("state") in (0, 1) for motor in motors)
        self.update_interval = timedelta(
            seconds=MOVING_UPDATE_INTERVAL_SECONDS if moving else UPDATE_INTERVAL_SECONDS
        )
        return data
