"""Switch platform for ISYGLT IPMS."""

from __future__ import annotations

from typing import Any

from homeassistant.components.switch import SwitchEntity, SwitchDeviceClass
from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity_platform import AddEntitiesCallback

from .const import DOMAIN
from .entity import IsygltEntity


async def async_setup_entry(
    hass: HomeAssistant,
    entry: ConfigEntry,
    async_add_entities: AddEntitiesCallback,
) -> None:
    """Set up configured switches."""
    data = hass.data[DOMAIN][entry.entry_id]
    async_add_entities(
        IsygltSwitch(data["coordinator"], config, data["areas"])
        for config in data["manifest"]["entities"]
        if config["platform"] == "switch"
    )


class IsygltSwitch(IsygltEntity, SwitchEntity):
    """An IPMS bit switch."""

    def __init__(self, coordinator: Any, config: dict[str, Any], areas: dict[str, str]) -> None:
        super().__init__(coordinator, config, areas)
        device_class = config.get("device_class")
        self._attr_device_class = (
            SwitchDeviceClass(device_class) if device_class is not None else None
        )

    @property
    def is_on(self) -> bool | None:
        """Return the current state bit."""
        return self.coordinator.get_bit(self.entity_config["state_marker"])

    async def async_turn_on(self, **kwargs: Any) -> None:
        """Switch the configured command bit on."""
        await self.coordinator.async_write_bit(
            self.entity_config.get("command_marker", self.entity_config["state_marker"]),
            True,
        )

    async def async_turn_off(self, **kwargs: Any) -> None:
        """Switch the configured command bit off."""
        await self.coordinator.async_write_bit(
            self.entity_config.get("command_marker", self.entity_config["state_marker"]),
            False,
        )
