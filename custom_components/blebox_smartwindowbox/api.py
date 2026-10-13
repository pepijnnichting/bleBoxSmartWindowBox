from __future__ import annotations

import asyncio
from dataclasses import dataclass
from typing import Any

import aiohttp


class BleBoxApiError(Exception):
    """Raised on any API/transport error."""


@dataclass
class DeviceInfo:
    device_name: str
    device_type: str
    api_level: str
    hw: str | None
    fw: str | None
    device_id: str
    ip: str | None


class BleBoxSmartWindowBoxApi:
    def __init__(self, session: aiohttp.ClientSession, host: str, timeout: float = 8.0) -> None:
        self._session = session
        self._host = host.rstrip("/")
        self._timeout = aiohttp.ClientTimeout(total=timeout)
        self._lock = asyncio.Lock()

    @property
    def base_url(self) -> str:
        return f"http://{self._host}"

    async def _get_json(self, path: str) -> Any:
        url = f"{self.base_url}{path}"
        try:
            async with self._lock:
                async with self._session.get(url, timeout=self._timeout) as resp:
                    if resp.status != 200:
                        raise BleBoxApiError(f"GET {path} failed: HTTP {resp.status}")
                    data = await resp.json(content_type=None)
                    if not isinstance(data, dict):
                        raise BleBoxApiError(f"GET {path} failed: expected a JSON object")
                    return data
        except (aiohttp.ClientError, asyncio.TimeoutError, ValueError) as e:
            raise BleBoxApiError(f"GET {path} failed: {e}") from e

    async def device_state(self) -> DeviceInfo:
        data = await self._get_json("/api/device/state")
        dev = data.get("device", {})
        return DeviceInfo(
            device_name=str(dev.get("deviceName", "")),
            device_type=str(dev.get("type", "")),
            api_level=str(dev.get("apiLevel", "")),
            hw=dev.get("hv"),
            fw=dev.get("fv"),
            device_id=str(dev.get("id", "")),
            ip=dev.get("ip"),
        )

    async def window_extended_state(self) -> dict[str, Any]:
        return await self._get_json("/api/window/extended/state")

    async def send_motor_command(self, channel: int, command: str) -> dict[str, Any]:
        return await self._get_json(f"/s/{channel}/{command}")
