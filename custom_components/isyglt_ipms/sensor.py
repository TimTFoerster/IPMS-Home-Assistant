"""Sensor platform for ISYGLT IPMS."""

from __future__ import annotations

from typing import Any

from homeassistant.components.sensor import (
    SensorDeviceClass,
    SensorEntity,
    SensorStateClass,
)
from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity_platform import AddEntitiesCallback

from .const import DOMAIN
from .entity import IsygltEntity
from .numeric import decode_numeric, scaled_value


async def async_setup_entry(
    hass: HomeAssistant,
    entry: ConfigEntry,
    async_add_entities: AddEntitiesCallback,
) -> None:
    """Set up configured sensors."""
    data = hass.data[DOMAIN][entry.entry_id]
    async_add_entities(
        IsygltSensor(data["coordinator"], config, data["areas"])
        for config in data["manifest"]["entities"]
        if config["platform"] == "sensor"
    )


class IsygltSensor(IsygltEntity, SensorEntity):
    """A byte/word sensor, optionally combining high and low bytes."""

    def __init__(self, coordinator: Any, config: dict[str, Any], areas: dict[str, str]) -> None:
        super().__init__(coordinator, config, areas)
        self._mode = config.get("mode", "byte")
        self._scale = float(config.get("scale", 1.0))
        self._offset = float(config.get("offset", 0.0))
        self._attr_native_unit_of_measurement = config.get("unit_of_measurement")
        device_class = config.get("device_class")
        self._attr_device_class = (
            SensorDeviceClass(device_class) if device_class is not None else None
        )
        self._attr_state_class = SensorStateClass.MEASUREMENT

    @property
    def native_value(self) -> float | int | None:
        """Return the decoded sensor value."""
        config = self.entity_config
        high_marker = config.get("high_marker")
        low_marker = config.get("low_marker")
        if high_marker is not None or low_marker is not None:
            if high_marker is None or low_marker is None:
                return None
            high = self.coordinator.get_byte(high_marker)
            low = self.coordinator.get_byte(low_marker)
            raw = None
        else:
            marker = config["state_marker"]
            raw = (
                self.coordinator.get_byte(marker)
                if self._mode in ("byte", "signed_byte")
                else self.coordinator.get_int(marker)
            )
            high = low = None

        decoded = decode_numeric(raw, self._mode, high=high, low=low)
        return scaled_value(decoded, self._scale, self._offset)
