"""Climate platform for ISYGLT IPMS."""

from __future__ import annotations

from typing import Any

from homeassistant.components.climate import ClimateEntity
from homeassistant.components.climate.const import (
    HVACAction,
    HVACMode,
    PRESET_COMFORT,
    PRESET_NONE,
    ClimateEntityFeature,
)
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
        self._temperature_scale = float(config.get("temperature_scale", 1.0))
        self._attr_temperature_unit = UnitOfTemperature.CELSIUS
        self._attr_min_temp = float(config.get("min_temp", 5))
        self._attr_max_temp = float(config.get("max_temp", 35))
        self._attr_target_temperature_step = float(
            config.get("temperature_step", self._temperature_scale)
        )
        self._attr_hvac_modes = [HVACMode.HEAT]
        self._attr_supported_features = ClimateEntityFeature.TARGET_TEMPERATURE
        if config.get("comfort_marker"):
            self._attr_supported_features |= ClimateEntityFeature.PRESET_MODE
            self._attr_preset_modes = [PRESET_COMFORT, PRESET_NONE]

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
    def hvac_action(self) -> HVACAction | None:
        """Report whether the heating valve is active."""
        marker = self.entity_config.get("valve_marker")
        if marker is None:
            return None
        is_heating = self.coordinator.get_bit(marker)
        if is_heating is None:
            return None
        return HVACAction.HEATING if is_heating else HVACAction.IDLE

    @property
    def preset_mode(self) -> str | None:
        """Return the comfort preset state when a comfort marker is configured."""
        marker = self.entity_config.get("comfort_marker")
        if marker is None:
            return None
        return (
            PRESET_COMFORT
            if self.coordinator.get_bit(marker)
            else PRESET_NONE
        )

    @property
    def hvac_mode(self) -> HVACMode:
        """Return the supported heating mode; comfort is a separate preset."""
        return HVACMode.HEAT

    async def async_set_temperature(self, **kwargs: Any) -> None:
        """Write a scaled target temperature."""
        temperature = kwargs.get(ATTR_TEMPERATURE)
        if temperature is None:
            return
        raw = round(float(temperature) / self._temperature_scale)
        await self.coordinator.async_write_byte(
            self.entity_config["target_temp_marker"], raw
        )

    async def async_set_preset_mode(self, preset_mode: str) -> None:
        """Enable or disable comfort using the configured comfort marker."""
        marker = self.entity_config.get("comfort_marker")
        if marker is not None:
            await self.coordinator.async_write_bit(
                marker, preset_mode == PRESET_COMFORT
            )
