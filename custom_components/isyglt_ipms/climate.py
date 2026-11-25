"""Climate platform for ISYGLT IPMS."""

from __future__ import annotations

from typing import Any

from homeassistant.components.climate import ClimateEntity
from homeassistant.components.climate.const import HVACMode
from homeassistant.config_entries import ConfigEntry
from homeassistant.const import ATTR_TEMPERATURE, UnitOfTemperature
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity_platform import AddEntitiesCallback

from .const import DOMAIN
from .entity import IsygltEntity


async def async_setup_entry(
    hass: HomeAssistant,
    entry: ConfigEntry,
    async_add_entities: AddEntitiesCallback,
) -> None:
    """Set up configured climate entities."""
    data = hass.data[DOMAIN][entry.entry_id]
    async_add_entities(
        IsygltClimate(data["coordinator"], config, data["areas"])
        for config in data["manifest"]["entities"]
        if config["platform"] == "climate"
    )


class IsygltClimate(IsygltEntity, ClimateEntity):
    """A simple IPMS room thermostat."""

    def __init__(self, coordinator: Any, config: dict[str, Any], areas: dict[str, str]) -> None:
        super().__init__(coordinator, config, areas)
        self._temperature_scale = float(config.get("temperature_scale", 0.5))
        self._attr_temperature_unit = UnitOfTemperature.CELSIUS
        self._attr_min_temp = float(config.get("min_temp", 5))
        self._attr_max_temp = float(config.get("max_temp", 35))
        self._attr_target_temperature_step = float(
            config.get("temperature_step", self._temperature_scale)
        )
        self._attr_hvac_modes = (
            [HVACMode.HEAT, HVACMode.AUTO]
            if config.get("comfort_marker")
            else [HVACMode.HEAT]
        )

    @property
    def current_temperature(self) -> float | None:
        """Return the scaled measured room temperature."""
        raw = self.coordinator.get_byte(self.entity_config["current_temp_marker"])
        return raw * self._temperature_scale if raw is not None else None

    @property
    def target_temperature(self) -> float | None:
        """Return the scaled target temperature."""
        raw = self.coordinator.get_byte(self.entity_config["target_temp_marker"])
        return raw * self._temperature_scale if raw is not None else None

    @property
    def hvac_mode(self) -> HVACMode:
        """Return the state of the optional comfort bit."""
        marker = self.entity_config.get("comfort_marker")
        if marker is None or self.coordinator.get_bit(marker):
            return HVACMode.HEAT
        return HVACMode.AUTO

    async def async_set_temperature(self, **kwargs: Any) -> None:
        """Write a scaled target temperature."""
        temperature = kwargs.get(ATTR_TEMPERATURE)
        if temperature is None:
            return
        raw = round(float(temperature) / self._temperature_scale)
        await self.coordinator.async_write_byte(
            self.entity_config["target_temp_marker"], raw
        )

    async def async_set_hvac_mode(self, hvac_mode: HVACMode) -> None:
        """Enable or disable comfort heating when the marker is configured."""
        marker = self.entity_config.get("comfort_marker")
        if marker is not None:
            await self.coordinator.async_write_bit(
                marker, hvac_mode == HVACMode.HEAT
            )
