"""Cover platform for ISYGLT IPMS."""

from __future__ import annotations

import asyncio
from typing import Any

from homeassistant.components.cover import (
    CoverDeviceClass,
    CoverEntity,
    CoverEntityFeature,
)
from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity_platform import AddEntitiesCallback

from .const import DOMAIN, DEFAULT_PULSE_MS
from .entity import IsygltEntity


async def async_setup_entry(
    hass: HomeAssistant,
    entry: ConfigEntry,
    async_add_entities: AddEntitiesCallback,
) -> None:
    """Set up configured covers."""
    data = hass.data[DOMAIN][entry.entry_id]
    async_add_entities(
        IsygltCover(data["coordinator"], config, data["areas"])
        for config in data["manifest"]["entities"]
        if config["platform"] == "cover"
    )


class IsygltCover(IsygltEntity, CoverEntity):
    """An IPMS momentary-command cover."""

    def __init__(self, coordinator: Any, config: dict[str, Any], areas: dict[str, str]) -> None:
        super().__init__(coordinator, config, areas)
        device_class = config.get("device_class")
        self._attr_device_class = (
            CoverDeviceClass(device_class) if device_class is not None else None
        )
        features = CoverEntityFeature.OPEN | CoverEntityFeature.CLOSE | CoverEntityFeature.STOP
        if config.get("tilt_step_ms") is not None:
            features |= CoverEntityFeature.OPEN_TILT | CoverEntityFeature.CLOSE_TILT
        self._attr_supported_features = features
        self._last_direction: str | None = None
        self._tilt_lock = asyncio.Lock()

    @property
    def is_opening(self) -> bool | None:
        """Return the configured moving-up marker."""
        marker = self.entity_config.get("moving_up_marker")
        return self.coordinator.get_bit(marker) if marker else None

    @property
    def is_closing(self) -> bool | None:
        """Return the configured moving-down marker."""
        marker = self.entity_config.get("moving_down_marker")
        return self.coordinator.get_bit(marker) if marker else None

    async def async_open_cover(self, **kwargs: Any) -> None:
        """Pulse the up marker."""
        self._last_direction = "up"
        await self.coordinator.async_pulse(
            self.entity_config["up_marker"],
            self.coordinator.master.get("pulse_ms", DEFAULT_PULSE_MS),
        )

    async def async_close_cover(self, **kwargs: Any) -> None:
        """Pulse the down marker."""
        self._last_direction = "down"
        await self.coordinator.async_pulse(
            self.entity_config["down_marker"],
            self.coordinator.master.get("pulse_ms", DEFAULT_PULSE_MS),
        )

    async def async_stop_cover(self, **kwargs: Any) -> None:
        """Pulse the active direction, or up when direction is unknown."""
        if self.entity_config.get("moving_up_marker") or self.entity_config.get(
            "moving_down_marker"
        ):
            direction = (
                "up"
                if self.entity_config.get("moving_up_marker")
                and self.coordinator.get_bit(self.entity_config["moving_up_marker"])
                else "down"
                if self.entity_config.get("moving_down_marker")
                and self.coordinator.get_bit(self.entity_config["moving_down_marker"])
                else self._last_direction or "up"
            )
        else:
            direction = self._last_direction or "up"
        marker = self.entity_config[f"{direction}_marker"]
        await self.coordinator.async_pulse(
            marker, self.coordinator.master.get("pulse_ms", DEFAULT_PULSE_MS)
        )

    async def async_open_cover_tilt(self, **kwargs: Any) -> None:
        """Make one timed upward slat movement bounded by a stop impulse."""
        await self._async_tilt("up")

    async def async_close_cover_tilt(self, **kwargs: Any) -> None:
        """Make one timed downward slat movement bounded by a stop impulse."""
        await self._async_tilt("down")

    async def _async_tilt(self, direction: str) -> None:
        marker = self.entity_config[f"{direction}_marker"]
        pulse_ms = self.coordinator.master.get("pulse_ms", DEFAULT_PULSE_MS)
        async with self._tilt_lock:
            self._last_direction = direction
            await self.coordinator.async_pulse(marker, pulse_ms)
            await asyncio.sleep(self.entity_config["tilt_step_ms"] / 1000)
            await self.coordinator.async_pulse(marker, pulse_ms)
