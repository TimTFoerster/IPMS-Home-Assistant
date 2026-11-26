"""ISYGLT IPMS integration."""

from __future__ import annotations

from typing import Any

from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant
from homeassistant.exceptions import ConfigEntryError
from homeassistant.helpers import area_registry

from .api import IsygltApiClient
from .config_flow import build_ipms_url, get_config_entry_settings
from .const import (
    DEFAULT_PULSE_MS,
    DEFAULT_SCAN_INTERVAL,
    CONF_MANIFEST_PATH,
    CONF_MASTER_ALIAS,
    CONF_PULSE_MS,
    CONF_SCAN_INTERVAL,
    DOMAIN,
    PLATFORMS,
)
from .coordinator import IsygltDataUpdateCoordinator
from .manifest_loader import (
    async_load_manifest,
    resolve_manifest_path,
    validate_manifest_master,
)


async def async_setup_entry(hass: HomeAssistant, entry: ConfigEntry) -> bool:
    """Set up an IPMS master from a manifest."""
    settings = get_config_entry_settings(entry)
    manifest_path = resolve_manifest_path(
        hass.config.config_dir, settings[CONF_MANIFEST_PATH]
    )
    manifest = await async_load_manifest(hass, manifest_path)

    master = manifest["master"]
    master["alias"] = settings[CONF_MASTER_ALIAS]
    validate_manifest_master(manifest, master["alias"])
    master["ipms_url"] = build_ipms_url(settings)
    master["scan_interval"] = settings.get(
        CONF_SCAN_INTERVAL, DEFAULT_SCAN_INTERVAL
    )
    master["pulse_ms"] = settings.get(CONF_PULSE_MS, DEFAULT_PULSE_MS)

    api = IsygltApiClient(hass, master["ipms_url"])
    coordinator = IsygltDataUpdateCoordinator(
        hass, api, manifest, master, config_entry=entry
    )
    await coordinator.async_config_entry_first_refresh()
    await _async_create_areas(hass, manifest)

    hass.data.setdefault(DOMAIN, {})[entry.entry_id] = {
        "api": api,
        "coordinator": coordinator,
        "manifest": manifest,
        "areas": _area_ids(hass, manifest),
    }
    await hass.config_entries.async_forward_entry_setups(entry, PLATFORMS)
    return True


async def async_unload_entry(hass: HomeAssistant, entry: ConfigEntry) -> bool:
    """Unload an IPMS master."""
    if not await hass.config_entries.async_unload_platforms(entry, PLATFORMS):
        return False
    hass.data.get(DOMAIN, {}).pop(entry.entry_id, None)
    return True


async def _async_create_areas(
    hass: HomeAssistant, manifest: dict[str, Any]
) -> None:
    """Create missing Home Assistant areas for manifest locations."""
    registry = area_registry.async_get(hass)
    for location in manifest.get("locations", []):
        area_name = location.get("name") or location.get("room")
        if not area_name:
            raise ConfigEntryError(
                f"Location {location.get('id', '<unknown>')} must define name or room"
            )
        if registry.async_get_area_by_name(area_name) is None:
            registry.async_create(area_name)


def _area_ids(hass: HomeAssistant, manifest: dict[str, Any]) -> dict[str, str]:
    """Return location IDs mapped to Home Assistant area IDs."""
    registry = area_registry.async_get(hass)
    result: dict[str, str] = {}
    for location in manifest.get("locations", []):
        area_name = location.get("name") or location.get("room")
        area = registry.async_get_area_by_name(area_name)
        if area is not None:
            result[location["id"]] = area.id
    return result
