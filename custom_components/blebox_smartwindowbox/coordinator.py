from __future__ import annotations

from datetime import timedelta
from typing import Any

from homeassistant.core import HomeAssistant
from homeassistant.helpers.device_registry import DeviceInfo
from homeassistant.helpers.update_coordinator import DataUpdateCoordinator, UpdateFailed

from .api import BleBoxSmartWindowBoxApi, BleBoxApiError
from .const import DEFAULT_NAME, DOMAIN, MOVING_UPDATE_INTERVAL_SECONDS, UPDATE_INTERVAL_SECONDS


class BleBoxCoordinator(DataUpdateCoordinator[dict[str, Any]]):
    def __init__(self, hass: HomeAssistant, api: BleBoxSmartWindowBoxApi) -> None:
        super().__init__(
            hass,
            logger=__import__("logging").getLogger(__name__),
            name=DOMAIN,
            update_interval=timedelta(seconds=UPDATE_INTERVAL_SECONDS),
        )
        self.api = api
        self.device_info: DeviceInfo | None = None

    async def _async_setup(self) -> None:
        """Fetch stable device metadata once when setting up the integration."""
        try:
            info = await self.api.device_state()
        except BleBoxApiError as err:
            raise UpdateFailed(str(err)) from err
        if not info.device_id:
            raise UpdateFailed("BleBox returned no device id")
        self.device_info = DeviceInfo(
            identifiers={(DOMAIN, info.device_id)},
            name=info.device_name or DEFAULT_NAME,
            manufacturer="BleBox",
            model=info.device_type or "smartWindowBox",
            hw_version=info.hw,
            sw_version=info.fw,
            configuration_url=self.api.base_url,
        )

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
