"""Light platform for ISYGLT IPMS."""

from __future__ import annotations

from typing import Any

from homeassistant.components.light import (
    ATTR_BRIGHTNESS,
    ATTR_COLOR_TEMP_KELVIN,
    ATTR_RGB_COLOR,
    ATTR_RGBW_COLOR,
    ColorMode,
    LightEntity,
)
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
    """Set up configured lights."""
    data = hass.data[DOMAIN][entry.entry_id]
    async_add_entities(
        IsygltLight(data["coordinator"], config, data["areas"])
        for config in data["manifest"]["entities"]
        if config["platform"] == "light"
    )


class IsygltLight(IsygltEntity, LightEntity):
    """An IPMS light with bit, byte-dimmer, TW, RGB, or RGBW control."""

    def __init__(self, coordinator: Any, config: dict[str, Any], areas: dict[str, str]) -> None:
        super().__init__(coordinator, config, areas)
        self._mode = config["mode"]
        self._last_brightness = int(config.get("default_brightness", 255))
        self._last_rgb: tuple[int, ...] = (255, 255, 255, 255)
        self._attr_supported_color_modes = {
            "bit": {ColorMode.ONOFF},
            "dimmer": {ColorMode.BRIGHTNESS},
            "dimmer_tw": {ColorMode.COLOR_TEMP},
            "rgb": {ColorMode.RGB},
            "rgbw": {ColorMode.RGBW},
        }[self._mode]
        self._attr_color_mode = next(iter(self._attr_supported_color_modes))
        if self._mode == "dimmer_tw":
            self._min_kelvin = int(config.get("min_kelvin", 2700))
            self._max_kelvin = int(config.get("max_kelvin", 6500))
            self._attr_min_color_temp_kelvin = self._min_kelvin
            self._attr_max_color_temp_kelvin = self._max_kelvin

    @property
    def brightness(self) -> int | None:
        """Return brightness from the configured byte marker."""
        if self._mode == "bit":
            return None
        if self._mode in ("rgb", "rgbw"):
            color = self._read_color()
            if not self.entity_config.get("brightness_marker"):
                return max(color) if color is not None else None
        value = self.coordinator.get_byte(self.entity_config["brightness_marker"])
        if self._mode in ("dimmer", "dimmer_tw") and value is not None and value > 0:
            self._last_brightness = value
        return value

    @property
    def color_temp_kelvin(self) -> int | None:
        """Return the linearly scaled color temperature."""
        if self._mode != "dimmer_tw":
            return None
        value = self.coordinator.get_byte(self.entity_config["color_temp_marker"])
        if value is None:
            return None
        minimum = self._min_kelvin
        maximum = self._max_kelvin
        return round(minimum + value / 255 * (maximum - minimum))

    @property
    def rgb_color(self) -> tuple[int, int, int] | None:
        """Return configured RGB marker values."""
        if self._mode != "rgb":
            return None
        color = self._read_color()
        if color is None:
            return None
        return (color[0], color[1], color[2])

    @property
    def rgbw_color(self) -> tuple[int, int, int, int] | None:
        """Return configured RGBW marker values."""
        if self._mode != "rgbw":
            return None
        color = self._read_color()
        if color is None:
            return None
        return (color[0], color[1], color[2], color[3])

    @property
    def is_on(self) -> bool | None:
        """Return the appropriate bit/byte/color on state."""
        if self._mode == "bit":
            return self.coordinator.get_bit(self.entity_config["state_marker"])
        if self._mode in ("dimmer", "dimmer_tw") or self.entity_config.get(
            "brightness_marker"
        ):
            brightness = self.brightness
            return brightness > 0 if brightness is not None else None
        color = self._read_color()
        return any(color) if color is not None else None

    async def async_turn_on(self, **kwargs: Any) -> None:
        """Turn on or update the light."""
        if self._mode == "bit":
            await self.coordinator.async_write_bit(
                self.entity_config.get(
                    "command_marker", self.entity_config["state_marker"]
                ),
                True,
                optimistic_marker=self.entity_config["state_marker"],
            )
        elif self._mode == "dimmer":
            brightness = kwargs.get(ATTR_BRIGHTNESS)
            if brightness is None or brightness <= 0:
                brightness = self._last_brightness
            await self.coordinator.async_write_byte(
                self.entity_config["brightness_marker"],
                int(brightness),
                optimistic_marker=self.entity_config["brightness_marker"],
            )
            self._last_brightness = int(brightness)
        elif self._mode == "dimmer_tw":
            await self._turn_on_tunable_white(kwargs)
        else:
            await self._turn_on_rgb(kwargs)

    async def async_turn_off(self, **kwargs: Any) -> None:
        """Turn off while retaining the last positive dimmer brightness."""
        if self._mode == "bit":
            await self.coordinator.async_write_bit(
                self.entity_config.get(
                    "command_marker", self.entity_config["state_marker"]
                ),
                False,
                optimistic_marker=self.entity_config["state_marker"],
            )
        elif self._mode in ("dimmer", "dimmer_tw") or self.entity_config.get(
            "brightness_marker"
        ):
            current = self.brightness
            if current is not None and current > 0:
                self._last_brightness = current
            await self.coordinator.async_write_byte(
                self.entity_config["brightness_marker"],
                0,
                optimistic_marker=self.entity_config["brightness_marker"],
            )
        else:
            for field in self._color_fields():
                await self.coordinator.async_write_byte(
                    self.entity_config[field],
                    0,
                    optimistic_marker=self.entity_config[field],
                )

    async def _turn_on_tunable_white(self, kwargs: dict[str, Any]) -> None:
        brightness = kwargs.get(ATTR_BRIGHTNESS)
        if brightness is None and not self.is_on:
            brightness = self._last_brightness
        if brightness is not None:
            await self.coordinator.async_write_byte(
                self.entity_config["brightness_marker"],
                int(brightness),
                optimistic_marker=self.entity_config["brightness_marker"],
            )
        temperature = kwargs.get(ATTR_COLOR_TEMP_KELVIN)
        if temperature is not None:
            minimum = self._min_kelvin
            maximum = self._max_kelvin
            normalized = round((int(temperature) - minimum) / (maximum - minimum) * 255)
            await self.coordinator.async_write_byte(
                self.entity_config["color_temp_marker"],
                max(0, min(255, normalized)),
                optimistic_marker=self.entity_config["color_temp_marker"],
            )

    async def _turn_on_rgb(self, kwargs: dict[str, Any]) -> None:
        color_key = ATTR_RGB_COLOR if self._mode == "rgb" else ATTR_RGBW_COLOR
        color = kwargs.get(color_key)
        if color is None:
            current_color = self._read_color()
            color = (
                current_color
                if current_color and any(current_color)
                else self._last_rgb[: len(self._color_fields())]
            )
        color = tuple(int(value) for value in color)
        fields = self._color_fields()
        self._last_rgb = color
        brightness = kwargs.get(ATTR_BRIGHTNESS)
        brightness_marker = self.entity_config.get("brightness_marker")
        if brightness_marker:
            if brightness is not None:
                await self.coordinator.async_write_byte(
                    brightness_marker,
                    int(brightness),
                    optimistic_marker=brightness_marker,
                )
        elif brightness is not None:
            color = tuple(round(component * int(brightness) / 255) for component in color)
        for field, component in zip(fields, color, strict=True):
            await self.coordinator.async_write_byte(
                self.entity_config[field],
                component,
                optimistic_marker=self.entity_config[field],
            )

    def _color_fields(self) -> tuple[str, ...]:
        if self._mode == "rgb":
            return ("red_marker", "green_marker", "blue_marker")
        return ("red_marker", "green_marker", "blue_marker", "white_marker")

    def _read_color(self) -> tuple[int, ...] | None:
        values = [
            self.coordinator.get_byte(self.entity_config[field])
            for field in self._color_fields()
        ]
        if any(value is None for value in values):
            return None
        color = tuple(value for value in values if value is not None)
        if any(color):
            self._last_rgb = color
        return color
