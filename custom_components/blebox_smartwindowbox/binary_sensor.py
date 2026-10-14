from __future__ import annotations

from typing import Any

from homeassistant.components.binary_sensor import BinarySensorEntity
from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity_platform import AddEntitiesCallback
from homeassistant.helpers.update_coordinator import CoordinatorEntity

from .const import DOMAIN
from .coordinator import BleBoxCoordinator


def _sensor_list(data: dict[str, Any]) -> list[dict[str, Any]]:
    return (data.get("window") or {}).get("sensors") or []


async def async_setup_entry(
    hass: HomeAssistant, entry: ConfigEntry, async_add_entities: AddEntitiesCallback
) -> None:
    coordinator: BleBoxCoordinator = hass.data[DOMAIN][entry.entry_id]["coordinator"]
    sensors = _sensor_list(coordinator.data)

    entities: list[BinarySensorEntity] = []

    # Only add rain sensor (your device has type "rain")
    for s in sensors:
        if str(s.get("type")) == "rain":
            entities.append(BleBoxRainBinarySensor(coordinator, entry.entry_id, int(s.get("id", 0))))

    async_add_entities(entities)


class BleBoxRainBinarySensor(CoordinatorEntity[BleBoxCoordinator], BinarySensorEntity):
    _attr_has_entity_name = True
    _attr_device_class = "moisture"  # best fit for rain detection

    def __init__(self, coordinator: BleBoxCoordinator, entry_id: str, sensor_id: int) -> None:
        super().__init__(coordinator)
        self._attr_device_info = coordinator.device_info
        self._entry_id = entry_id
        self._sensor_id = sensor_id
        self._attr_unique_id = f"{entry_id}_rain_{sensor_id}"
        self._attr_name = "Rain"

    def _sensor(self) -> dict[str, Any] | None:
        for s in (self.coordinator.data.get("window") or {}).get("sensors") or []:
            if str(s.get("type")) == "rain" and int(s.get("id", -1)) == self._sensor_id:
                return s
        return None

    @property
    def is_on(self) -> bool | None:
        s = self._sensor()
        if not s:
            return None
        # Your payload: rain.value == 1 when raining
        return s.get("value") == 1

    @property
    def extra_state_attributes(self) -> dict[str, Any]:
        s = self._sensor() or {}
        return {
            "state": s.get("state"),
            "trend": s.get("trend"),
            "elapsedTimeS": s.get("elapsedTimeS"),
            "iconSet": s.get("iconSet"),
        }
