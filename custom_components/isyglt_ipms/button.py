"""Button platform for ISYGLT IPMS."""

from __future__ import annotations

from typing import Any

from homeassistant.components.button import ButtonEntity
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
    """Set up configured momentary buttons."""
    data = hass.data[DOMAIN][entry.entry_id]
    async_add_entities(
        IsygltButton(data["coordinator"], config, data["areas"])
        for config in data["manifest"]["entities"]
        if config["platform"] == "button"
    )


class IsygltButton(IsygltEntity, ButtonEntity):
    """An IPMS pulse button."""

    def __init__(self, coordinator: Any, config: dict[str, Any], areas: dict[str, str]) -> None:
        super().__init__(coordinator, config, areas)

    async def async_press(self) -> None:
        """Pulse the configured command marker."""
        await self.coordinator.async_pulse(
            self.entity_config["command_marker"],
            self.entity_config.get("pulse_ms"),
        )
