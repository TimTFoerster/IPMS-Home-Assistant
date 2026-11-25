"""Number platform for ISYGLT IPMS."""

from __future__ import annotations

from typing import Any

from homeassistant.components.number import (
    NumberDeviceClass,
    NumberEntity,
    NumberMode,
)
from homeassistant.config_entries import ConfigEntry
from homeassistant.const import EntityCategory
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity_platform import AddEntitiesCallback

from .const import DOMAIN
from .entity import IsygltEntity
from .numeric import decode_numeric, encode_numeric, scaled_value

_DEFAULTS = {
    "mode": "byte",
    "scale": 1.0,
    "offset": 0.0,
    "min_value": 0,
    "max_value": 255,
    "step": 1,
}


async def async_setup_entry(
    hass: HomeAssistant,
    entry: ConfigEntry,
    async_add_entities: AddEntitiesCallback,
) -> None:
    """Set up configured numeric parameters."""
    data = hass.data[DOMAIN][entry.entry_id]
    async_add_entities(
        IsygltNumber(data["coordinator"], config, data["areas"])
        for config in data["manifest"]["entities"]
        if config["platform"] == "number"
    )


class IsygltNumber(IsygltEntity, NumberEntity):
    """A read/write IPMS byte or word value."""

    def __init__(self, coordinator: Any, config: dict[str, Any], areas: dict[str, str]) -> None:
        super().__init__(coordinator, config, areas)
        settings = {**_DEFAULTS, **config}
        self._mode = settings["mode"]
        self._scale = float(settings["scale"])
        self._offset = float(settings["offset"])
        default_minimum, default_maximum = (
            (-128, 127)
            if self._mode == "signed_byte"
            else (-32768, 32767)
            if self._mode == "signed_word"
            else (0, 65535)
            if self._mode == "word"
            else (0, 255)
        )
        self._attr_native_min_value = float(
            config.get("min_value", default_minimum)
        )
        self._attr_native_max_value = float(
            config.get("max_value", default_maximum)
        )
        self._attr_native_step = float(settings["step"])
        self._attr_native_unit_of_measurement = settings.get("unit_of_measurement")
        self._attr_mode = NumberMode(settings.get("number_mode", NumberMode.BOX))
        category = settings.get("entity_category")
        self._attr_entity_category = (
            EntityCategory(category) if category is not None else None
        )
        device_class = settings.get("device_class")
        self._attr_device_class = (
            NumberDeviceClass(device_class) if device_class is not None else None
        )

    @property
    def native_value(self) -> float | None:
        """Return the scaled numeric marker value."""
        raw = self.coordinator.get_int(self.entity_config["marker"])
        decoded = decode_numeric(raw, self._mode)
        return scaled_value(decoded, self._scale, self._offset)

    async def async_set_native_value(self, value: float) -> None:
        """Write the selected byte/word value."""
        raw = encode_numeric(value, self._mode, self._scale, self._offset)
        if self._mode in ("byte", "signed_byte"):
            await self.coordinator.async_write_byte(self.entity_config["marker"], raw)
        else:
            await self.coordinator.async_write_word(self.entity_config["marker"], raw)
