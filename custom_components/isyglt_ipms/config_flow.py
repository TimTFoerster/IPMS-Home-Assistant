"""Config flow for ISYGLT IPMS."""

from __future__ import annotations

import logging
from pathlib import Path
from typing import Any

import voluptuous as vol
import yaml
from homeassistant import config_entries
from homeassistant.core import HomeAssistant
from homeassistant.exceptions import ConfigEntryError

from .api import IsygltApiClient, IsygltApiError
from .const import (
    CONF_IPMS_URL,
    CONF_MANIFEST_PATH,
    CONF_MASTER_ALIAS,
    CONF_PULSE_MS,
    CONF_SCAN_INTERVAL,
    DEFAULT_PULSE_MS,
    DEFAULT_SCAN_INTERVAL,
    DOMAIN,
)
from .manifest_loader import async_load_manifest

_LOGGER = logging.getLogger(__name__)


async def _validate_input(hass: HomeAssistant, data: dict[str, Any]) -> None:
    """Check manifest readability and verify the configured IPMS endpoint."""
    manifest_path = Path(data[CONF_MANIFEST_PATH])
    if manifest_path.is_absolute() or ".." in manifest_path.parts:
        raise ValueError(
            "Manifest path must be relative to the Home Assistant config directory"
        )
    await async_load_manifest(hass, Path(hass.config.config_dir) / manifest_path)
    api = IsygltApiClient(hass, data[CONF_IPMS_URL])
    await api.async_read_pool([])


class IsygltConfigFlow(config_entries.ConfigFlow, domain=DOMAIN):
    """Set up an IPMS master."""

    VERSION = 1

    async def async_step_user(
        self, user_input: dict[str, Any] | None = None
    ) -> config_entries.ConfigFlowResult:
        """Handle the initial setup form."""
        errors: dict[str, str] = {}
        if user_input is not None:
            try:
                await _validate_input(self.hass, user_input)
            except IsygltApiError as err:
                errors["base"] = "cannot_connect"
                _LOGGER.error("Unable to validate IPMS connection: %s", err)
            except (
                ConfigEntryError,
                OSError,
                UnicodeError,
                ValueError,
                yaml.YAMLError,
            ) as err:
                errors["base"] = "invalid_manifest"
                _LOGGER.error("Unable to validate IPMS manifest: %s", err)
            else:
                await self.async_set_unique_id(
                    f"{user_input[CONF_IPMS_URL].rstrip('/')}|"
                    f"{user_input[CONF_MASTER_ALIAS]}"
                )
                self._abort_if_unique_id_configured()
                return self.async_create_entry(
                    title=user_input[CONF_MASTER_ALIAS],
                    data={
                        **user_input,
                        CONF_IPMS_URL: user_input[CONF_IPMS_URL].rstrip("/"),
                    },
                )

        schema = vol.Schema(
            {
                vol.Required(CONF_IPMS_URL): str,
                vol.Required(CONF_MASTER_ALIAS, default="IPM1"): str,
                vol.Required(CONF_MANIFEST_PATH): str,
                vol.Optional(
                    CONF_SCAN_INTERVAL, default=DEFAULT_SCAN_INTERVAL
                ): vol.All(vol.Coerce(float), vol.Range(min=0.1)),
                vol.Optional(CONF_PULSE_MS, default=DEFAULT_PULSE_MS): vol.All(
                    vol.Coerce(int), vol.Range(min=1)
                ),
            }
        )
        return self.async_show_form(
            step_id="user", data_schema=schema, errors=errors
        )
