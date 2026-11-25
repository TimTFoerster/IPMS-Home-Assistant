"""Central batch-polling coordinator for an IPMS master."""

from __future__ import annotations

import asyncio
import logging
from datetime import timedelta
from typing import Any

from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant
from homeassistant.exceptions import ConfigEntryError
from homeassistant.helpers.update_coordinator import (
    DataUpdateCoordinator,
    UpdateFailed,
)

from .api import IsygltApiClient, IsygltApiError
from .const import DEFAULT_PULSE_MS, DEFAULT_SCAN_INTERVAL

_BIT_FIELDS = {
    "enable_marker",
    "fault_marker",
    "moving_up_marker",
    "moving_down_marker",
    "comfort_marker",
    "valve_marker",
}


class IsygltDataUpdateCoordinator(DataUpdateCoordinator[dict[str, Any]]):
    """Poll every entity's state markers in one IPMS request."""

    def __init__(
        self,
        hass: HomeAssistant,
        api: IsygltApiClient,
        manifest: dict[str, Any],
        master: dict[str, Any],
        config_entry: ConfigEntry | None = None,
    ) -> None:
        self.api = api
        self.manifest = manifest
        self.master = master
        self.markers = self._collect_markers(manifest.get("entities", []))
        self.master_online = False
        self._last_cycle: Any = None
        self._unchanged_cycles = 0
        self._cycle_key = self._master_cycle_key(master.get("alias", ""))
        self._refresh_lock = asyncio.Lock()
        super().__init__(
            hass,
            logger=logging.getLogger(__name__),
            config_entry=config_entry,
            name=f"ISYGLT IPMS {master.get('alias', '')}",
            update_interval=timedelta(
                seconds=master.get("scan_interval", DEFAULT_SCAN_INTERVAL)
            ),
        )

    @staticmethod
    def _collect_markers(entities: list[dict[str, Any]]) -> list[tuple[str, str]]:
        """Collect and deduplicate only markers required to read entity state."""
        markers: dict[str, str] = {}
        for entity in entities:
            platform = entity.get("platform", "")
            mode = entity.get("mode")
            fields: dict[str, str] = {}
            if platform == "light":
                if mode == "bit":
                    fields["state_marker"] = "BIT"
                else:
                    fields.update(
                        {
                            field: "BYTE"
                            for field in (
                                "brightness_marker",
                                "color_temp_marker",
                                "red_marker",
                                "green_marker",
                                "blue_marker",
                                "white_marker",
                            )
                        }
                    )
            elif platform in ("switch", "binary_sensor"):
                fields["state_marker"] = "BIT"
            elif platform == "cover":
                fields.update(
                    {
                        "moving_up_marker": "BIT",
                        "moving_down_marker": "BIT",
                    }
                )
            elif platform == "number":
                fields["marker"] = "BYTE"
            elif platform == "sensor":
                fields.update(
                    {
                        "state_marker": "BYTE",
                        "high_marker": "BYTE",
                        "low_marker": "BYTE",
                    }
                )
            elif platform == "climate":
                fields.update(
                    {
                        "current_temp_marker": "BYTE",
                        "target_temp_marker": "BYTE",
                        "comfort_marker": "BIT",
                        "valve_marker": "BIT",
                    }
                )
            fields.update({field: "BIT" for field in _BIT_FIELDS})
            for field, value_type in fields.items():
                marker = entity.get(field)
                if marker is None:
                    continue
                if not isinstance(marker, str):
                    raise ConfigEntryError(
                        f"Entity {entity.get('id', '<unknown>')!r} field "
                        f"{field!r} must be a string marker"
                    )
                previous_type = markers.get(marker)
                if previous_type is not None and previous_type != value_type:
                    raise ConfigEntryError(
                        f"IPMS marker {marker!r} is configured as both "
                        f"{previous_type} and {value_type}"
                    )
                markers[marker] = value_type
        return list(markers.items())

    @staticmethod
    def _master_cycle_key(alias: str) -> str:
        """Translate an alias such as IPM1 into its cycle-set key IPM001."""
        if alias.startswith("IPM") and alias[3:].isdigit():
            return f"IPM{int(alias[3:]):03d}"
        return alias

    async def _async_update_data(self) -> dict[str, Any]:
        """Fetch all configured entity markers in one batch."""
        try:
            async with self._refresh_lock:
                result = await self.api.async_read_pool(self.markers)
        except IsygltApiError as err:
            self.master_online = False
            raise UpdateFailed(str(err)) from err

        cycle_set = result.get("masterCycleSet") or {}
        cycle = cycle_set.get(self._cycle_key)
        if result.get("poolStatus") == "COMM_ERROR" or cycle is None:
            self.master_online = False
        else:
            if cycle == self._last_cycle:
                self._unchanged_cycles += 1
            else:
                self._unchanged_cycles = 0
            self._last_cycle = cycle
            self.master_online = self._unchanged_cycles < 2
        return result.get("data", {})

    def get_bit(self, marker: str) -> bool | None:
        """Return a polled bit value, or None when missing/unparseable."""
        value = self.data.get(marker) if self.data else None
        if value is None:
            return None
        try:
            return int(value) != 0
        except (TypeError, ValueError):
            return None

    def get_byte(self, marker: str) -> int | None:
        """Return a polled byte value, or None when missing/unparseable."""
        value = self.get_int(marker)
        return value if value is not None and 0 <= value <= 255 else None

    def get_int(self, marker: str) -> int | None:
        """Return a polled integer value without imposing a byte range."""
        value = self.data.get(marker) if self.data else None
        if value is None:
            return None
        try:
            return int(value)
        except (TypeError, ValueError):
            return None

    async def async_write_bit(self, marker: str, value: bool) -> None:
        """Write a bit and immediately refresh all polled state."""
        command = "SET_BIT_SWITCH" if value else "CLR_BIT_SWITCH"
        await self.api.async_send_command(command, marker, 1)
        await self.async_request_refresh()

    async def async_write_byte(self, marker: str, value: int) -> None:
        """Write a byte and immediately refresh all polled state."""
        if not 0 <= value <= 255:
            raise ValueError("Byte value must be between 0 and 255")
        await self.api.async_send_command("WRITE_BYTE", marker, value)
        await self.async_request_refresh()

    async def async_write_word(self, marker: str, value: int) -> None:
        """Write a word and immediately refresh all polled state."""
        if not 0 <= value <= 65535:
            raise ValueError("Word value must be between 0 and 65535")
        await self.api.async_send_command("WRITE_WORD", marker, value)
        await self.async_request_refresh()

    async def async_pulse(self, marker: str, pulse_ms: int | None = None) -> None:
        """Pulse a bit and refresh state when the pulse has completed."""
        duration = self.master.get("pulse_ms", DEFAULT_PULSE_MS)
        if pulse_ms is not None:
            duration = pulse_ms
        await self.api.async_pulse_bit(marker, duration)
        await self.async_request_refresh()
