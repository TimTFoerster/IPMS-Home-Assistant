"""Config flow for ISYGLT IPMS."""

from __future__ import annotations

import asyncio
import errno
import logging
import socket
from typing import Any
from urllib.parse import urlsplit

import aiohttp
import voluptuous as vol
import yaml
from homeassistant import config_entries
from homeassistant.core import HomeAssistant, callback
from homeassistant.exceptions import ConfigEntryError

from .api import (
    IsygltApiClient,
    IsygltApiError,
    IsygltConnectionError,
    normalize_ipms_url,
)
from .const import (
    CONF_IPMS_API_PATH,
    CONF_IPMS_HOST,
    CONF_IPMS_PORT,
    CONF_IPMS_SCHEME,
    CONF_IPMS_URL,
    CONF_MANIFEST_PATH,
    CONF_MASTER_ALIAS,
    CONF_PULSE_MS,
    CONF_SCAN_INTERVAL,
    DEFAULT_IPMS_API_PATH,
    DEFAULT_IPMS_PORT,
    DEFAULT_IPMS_SCHEME,
    DEFAULT_MANIFEST_PATH,
    DEFAULT_PULSE_MS,
    DEFAULT_SCAN_INTERVAL,
    DOMAIN,
)
from .manifest_loader import (
    async_load_manifest,
    resolve_manifest_path,
    validate_manifest_master,
)

_LOGGER = logging.getLogger(__name__)


_CONNECTION_FIELDS = (
    CONF_IPMS_SCHEME,
    CONF_IPMS_HOST,
    CONF_IPMS_PORT,
    CONF_IPMS_API_PATH,
)
_OPTION_FIELDS = (
    *_CONNECTION_FIELDS,
    CONF_MASTER_ALIAS,
    CONF_MANIFEST_PATH,
    CONF_SCAN_INTERVAL,
    CONF_PULSE_MS,
)


def build_ipms_url(data: dict[str, Any]) -> str:
    """Build a normalized endpoint URL from the editable connection fields."""
    scheme = str(data[CONF_IPMS_SCHEME]).strip().lower()
    if scheme not in ("http://", "https://"):
        raise ValueError("Protocol must be http:// or https://")

    host = str(data[CONF_IPMS_HOST]).strip()
    if (
        not host
        or any(char.isspace() for char in host)
        or any(char in host for char in "/?#@")
    ):
        raise ValueError("Enter a host name or IP address without a port")
    if host.startswith("[") and host.endswith("]"):
        host = host[1:-1]
    try:
        socket.inet_pton(socket.AF_INET6, host)
    except OSError:
        if ":" in host:
            raise ValueError("IPv6 addresses must be entered without brackets")
        host_part = host
    else:
        host_part = f"[{host}]"

    port = int(data[CONF_IPMS_PORT])
    if not 1 <= port <= 65535:
        raise ValueError("Port must be between 1 and 65535")

    api_path = str(data[CONF_IPMS_API_PATH]).strip()
    if not api_path.startswith("/") or "?" in api_path or "#" in api_path:
        raise ValueError("API path must start with / and contain no query or fragment")
    return normalize_ipms_url(f"{scheme}{host_part}:{port}{api_path}")


def _config_entry_unique_id(ipms_url: str, master_alias: str) -> str:
    """Build a stable unique ID for a server and one of its IP masters."""
    return f"{normalize_ipms_url(ipms_url)}#{master_alias}"


def _entry_settings(entry: config_entries.ConfigEntry) -> dict[str, Any]:
    """Return effective settings, including legacy URL-only config entries."""
    settings = {**entry.data, **entry.options}
    if not all(field in settings for field in _CONNECTION_FIELDS):
        legacy_url = settings.get(CONF_IPMS_URL)
        if legacy_url:
            parsed = urlsplit(legacy_url)
            settings.setdefault(
                CONF_IPMS_SCHEME, f"{parsed.scheme.lower()}://"
            )
            settings.setdefault(CONF_IPMS_HOST, parsed.hostname or "")
            settings.setdefault(
                CONF_IPMS_PORT,
                parsed.port
                or (443 if parsed.scheme.lower() == "https" else DEFAULT_IPMS_PORT),
            )
            settings.setdefault(CONF_IPMS_API_PATH, parsed.path or DEFAULT_IPMS_API_PATH)
    settings.setdefault(CONF_IPMS_SCHEME, DEFAULT_IPMS_SCHEME)
    settings.setdefault(CONF_IPMS_HOST, "")
    settings.setdefault(CONF_IPMS_PORT, DEFAULT_IPMS_PORT)
    settings.setdefault(CONF_IPMS_API_PATH, DEFAULT_IPMS_API_PATH)
    settings.setdefault(CONF_MANIFEST_PATH, DEFAULT_MANIFEST_PATH)
    settings.setdefault(CONF_SCAN_INTERVAL, DEFAULT_SCAN_INTERVAL)
    settings.setdefault(CONF_PULSE_MS, DEFAULT_PULSE_MS)
    return settings


async def _validate_input(hass: HomeAssistant, data: dict[str, Any]) -> str:
    """Validate one master's manifest and endpoint; return its normalized URL."""
    ipms_url = build_ipms_url(data)
    manifest_path = resolve_manifest_path(
        hass.config.config_dir, data[CONF_MANIFEST_PATH]
    )
    manifest = await async_load_manifest(hass, manifest_path)
    validate_manifest_master(manifest, data[CONF_MASTER_ALIAS])
    api = IsygltApiClient(hass, ipms_url)
    await api.async_read_pool([(f"{data[CONF_MASTER_ALIAS]}:M0", "BYTE")])
    return ipms_url


def _connection_error_key(err: IsygltApiError) -> str:
    """Map network exceptions to the most useful user-facing diagnosis."""
    cause: BaseException | None = err.__cause__
    while cause is not None:
        if isinstance(cause, socket.gaierror):
            return "cannot_resolve_host"
        if isinstance(cause, asyncio.TimeoutError):
            return "connection_timeout"
        if isinstance(cause, aiohttp.ClientConnectorError):
            os_error = cause.os_error
            if isinstance(os_error, socket.gaierror):
                return "cannot_resolve_host"
            if getattr(os_error, "errno", None) == errno.ECONNREFUSED:
                return "connection_refused"
            if getattr(os_error, "errno", None) in (
                errno.ENETUNREACH,
                errno.EHOSTUNREACH,
            ):
                return "host_unreachable"
        cause = cause.__cause__
    if isinstance(err, IsygltConnectionError):
        return "invalid_ipms_response"
    return "cannot_connect"


def _manifest_error_key(err: ConfigEntryError) -> str:
    """Distinguish a missing manifest from a present but invalid file."""
    cause: BaseException | None = err.__cause__
    while cause is not None:
        if isinstance(cause, (FileNotFoundError, IsADirectoryError)):
            return "manifest_not_found"
        cause = cause.__cause__
    return "invalid_manifest"


def _form_schema(settings: dict[str, Any]) -> vol.Schema:
    """Create the shared setup/options form schema with usable defaults."""
    return vol.Schema(
        {
            vol.Required(
                CONF_IPMS_SCHEME,
                default=settings.get(CONF_IPMS_SCHEME, DEFAULT_IPMS_SCHEME),
            ): str,
            vol.Required(
                CONF_IPMS_HOST, default=settings.get(CONF_IPMS_HOST, "")
            ): str,
            vol.Required(
                CONF_IPMS_PORT, default=settings.get(CONF_IPMS_PORT, DEFAULT_IPMS_PORT)
            ): vol.All(vol.Coerce(int), vol.Range(min=1, max=65535)),
            vol.Required(
                CONF_IPMS_API_PATH,
                default=settings.get(CONF_IPMS_API_PATH, DEFAULT_IPMS_API_PATH),
            ): str,
            vol.Required(
                CONF_MASTER_ALIAS, default=settings.get(CONF_MASTER_ALIAS, "IPM1")
            ): str,
            vol.Required(
                CONF_MANIFEST_PATH,
                default=settings.get(CONF_MANIFEST_PATH, DEFAULT_MANIFEST_PATH),
            ): str,
            vol.Required(
                CONF_SCAN_INTERVAL,
                default=settings.get(CONF_SCAN_INTERVAL, DEFAULT_SCAN_INTERVAL),
            ): vol.All(vol.Coerce(float), vol.Range(min=0.1)),
            vol.Required(
                CONF_PULSE_MS,
                default=settings.get(CONF_PULSE_MS, DEFAULT_PULSE_MS),
            ): vol.All(vol.Coerce(int), vol.Range(min=1)),
        }
    )


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
                ipms_url = await _validate_input(self.hass, user_input)
            except IsygltApiError as err:
                errors["base"] = _connection_error_key(err)
                _LOGGER.error("Unable to validate IPMS connection: %s", err)
            except ValueError as err:
                errors["base"] = "invalid_connection_settings"
                _LOGGER.error("Unable to validate IPMS configuration: %s", err)
            except ConfigEntryError as err:
                errors["base"] = _manifest_error_key(err)
                _LOGGER.error("Unable to validate IPMS manifest: %s", err)
            except (OSError, UnicodeError, yaml.YAMLError) as err:
                errors["base"] = "invalid_manifest"
                _LOGGER.error("Unable to validate IPMS manifest: %s", err)
            else:
                await self.async_set_unique_id(
                    _config_entry_unique_id(
                        ipms_url, user_input[CONF_MASTER_ALIAS]
                    )
                )
                self._abort_if_unique_id_configured()
                return self.async_create_entry(
                    title=user_input[CONF_MASTER_ALIAS],
                    data={
                        **user_input,
                        CONF_IPMS_URL: ipms_url,
                    },
                )

        return self.async_show_form(
            step_id="user",
            data_schema=_form_schema(user_input or _entry_settings_defaults()),
            errors=errors,
        )

    @staticmethod
    @callback
    def async_get_options_flow(
        config_entry: config_entries.ConfigEntry,
    ) -> config_entries.OptionsFlow:
        """Return the editable settings flow."""
        return IsygltOptionsFlow()


def get_config_entry_settings(
    entry: config_entries.ConfigEntry,
) -> dict[str, Any]:
    """Return effective settings for setup, including legacy URL-only entries."""
    return _entry_settings(entry)


def _entry_settings_defaults() -> dict[str, Any]:
    """Return setup defaults without requiring an existing config entry."""
    return {
        CONF_IPMS_SCHEME: DEFAULT_IPMS_SCHEME,
        CONF_IPMS_HOST: "",
        CONF_IPMS_PORT: DEFAULT_IPMS_PORT,
        CONF_IPMS_API_PATH: DEFAULT_IPMS_API_PATH,
        CONF_MASTER_ALIAS: "IPM1",
        CONF_MANIFEST_PATH: DEFAULT_MANIFEST_PATH,
        CONF_SCAN_INTERVAL: DEFAULT_SCAN_INTERVAL,
        CONF_PULSE_MS: DEFAULT_PULSE_MS,
    }


class IsygltOptionsFlow(config_entries.OptionsFlowWithReload):
    """Change one master's connection and manifest settings."""

    async def async_step_init(
        self, user_input: dict[str, Any] | None = None
    ) -> config_entries.ConfigFlowResult:
        """Validate and save updated settings."""
        settings = _entry_settings(self.config_entry)
        errors: dict[str, str] = {}
        if user_input is not None:
            try:
                ipms_url = await _validate_input(self.hass, user_input)
            except IsygltApiError as err:
                errors["base"] = _connection_error_key(err)
                _LOGGER.error("Unable to validate updated IPMS connection: %s", err)
            except ValueError as err:
                errors["base"] = "invalid_connection_settings"
                _LOGGER.error("Invalid updated IPMS settings: %s", err)
            except ConfigEntryError as err:
                errors["base"] = _manifest_error_key(err)
                _LOGGER.error("Unable to validate updated IPMS manifest: %s", err)
            except (OSError, UnicodeError, yaml.YAMLError) as err:
                errors["base"] = "invalid_manifest"
                _LOGGER.error("Unable to validate updated IPMS manifest: %s", err)
            else:
                new_unique_id = _config_entry_unique_id(
                    ipms_url, user_input[CONF_MASTER_ALIAS]
                )
                for entry in self.hass.config_entries.async_entries(DOMAIN):
                    if entry.entry_id == self.config_entry.entry_id:
                        continue
                    other_settings = _entry_settings(entry)
                    try:
                        other_url = build_ipms_url(other_settings)
                    except (KeyError, TypeError, ValueError):
                        continue
                    if (
                        other_url == ipms_url
                        and other_settings.get(CONF_MASTER_ALIAS)
                        == user_input[CONF_MASTER_ALIAS]
                    ):
                        errors["base"] = "already_configured"
                        break
                if not errors:
                    self.hass.config_entries.async_update_entry(
                        self.config_entry,
                        unique_id=new_unique_id,
                        title=user_input[CONF_MASTER_ALIAS],
                    )
                    return self.async_create_entry(
                        title="",
                        data={
                            field: user_input[field]
                            for field in _OPTION_FIELDS
                        },
                    )

        return self.async_show_form(
            step_id="init",
            data_schema=_form_schema(user_input or settings),
            errors=errors,
        )
