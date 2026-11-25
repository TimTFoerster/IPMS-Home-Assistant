"""Asynchronous HTTP client for the ISYGLT IPMS API."""

from __future__ import annotations

import asyncio
from itertools import count
from typing import Any

import aiohttp
from homeassistant.core import HomeAssistant
from homeassistant.helpers.aiohttp_client import async_get_clientsession


class IsygltApiError(Exception):
    """Base exception for IPMS API failures."""


class IsygltConnectionError(IsygltApiError):
    """IPMS is unreachable or failed to return valid pool data."""


class IsygltCommandError(IsygltApiError):
    """IPMS rejected or failed to process a command."""


class IsygltApiClient:
    """Client for batch pool reads and URL-encoded ajax commands."""

    def __init__(self, hass: HomeAssistant, base_url: str) -> None:
        self._session = async_get_clientsession(hass)
        self._base_url = base_url.rstrip("/")
        self._command_ids = count(1)

    async def async_read_pool(
        self, markers: list[tuple[str, str]]
    ) -> dict[str, Any]:
        """Read a batch of BIT/BYTE markers from jsonpooldata."""
        request_data = []
        for marker, value_type in markers:
            if value_type not in ("BIT", "BYTE"):
                raise ValueError(f"Unsupported IPMS value type: {value_type}")
            request_data.append(
                {"masterMarker": marker, "valueType": value_type, "length": 1}
            )

        try:
            async with self._session.post(
                f"{self._base_url}/jsonpooldata",
                json={"reqDataSet": request_data},
                headers={"Content-Type": "application/json"},
            ) as response:
                response.raise_for_status()
                payload = await response.json()
        except (aiohttp.ClientError, asyncio.TimeoutError, ValueError) as err:
            raise IsygltConnectionError(f"Unable to read IPMS pool: {err}") from err

        if not isinstance(payload, dict) or not isinstance(
            payload.get("dataItems"), list
        ):
            raise IsygltConnectionError("IPMS returned an invalid jsonpooldata response")
        if payload.get("poolStatus") == "COMM_ERROR":
            raise IsygltConnectionError("IPMS returned COMM_ERROR while reading pool")
        if not isinstance(payload.get("masterCycleSet", {}), dict):
            raise IsygltConnectionError("IPMS returned an invalid masterCycleSet")

        return {
            "data": {
                item["m"]: item.get("v")
                for item in payload["dataItems"]
                if isinstance(item, dict) and isinstance(item.get("m"), str)
            },
            "poolStatus": payload.get("poolStatus"),
            "masterCycleSet": payload.get("masterCycleSet", {}),
        }

    async def async_send_command(self, cmd: str, marker: str, value: int) -> None:
        """Send a single command using the mandatory proxy query and form body."""
        if isinstance(value, bool) or not isinstance(value, int):
            raise ValueError("IPMS command VALUE must be an integer")
        try:
            async with self._session.post(
                f"{self._base_url}/ajaxcmdproxy",
                params={"$MCRQC$": "1"},
                data={
                    "CMD": cmd,
                    "ID": str(next(self._command_ids)),
                    "MARKER": marker,
                    "VALUE": str(value),
                },
                headers={"Content-Type": "application/x-www-form-urlencoded"},
            ) as response:
                response.raise_for_status()
                proxy_response = response.headers.get("$PROXY_RESPONSE$")
                body = await response.text()
        except (aiohttp.ClientError, asyncio.TimeoutError) as err:
            raise IsygltCommandError(f"Unable to send IPMS command: {err}") from err

        if proxy_response != "OK" or "COMM_ERROR" in body:
            raise IsygltCommandError(
                f"IPMS command failed (proxy response: {proxy_response!r})"
            )

    async def async_pulse_bit(self, marker: str, pulse_ms: int) -> None:
        """Set a bit, wait for the requested duration, then clear it."""
        await self.async_send_command("SET_BIT_SWITCH", marker, 1)
        await asyncio.sleep(pulse_ms / 1000)
        await self.async_send_command("CLR_BIT_SWITCH", marker, 1)
