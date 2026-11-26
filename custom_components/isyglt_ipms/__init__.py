"""ISYGLT IPMS integration."""

from __future__ import annotations

import re
from typing import Any

from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant
from homeassistant.exceptions import ConfigEntryError
from homeassistant.helpers import area_registry, floor_registry

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
    """Create or update Home Assistant floors and areas from manifest locations."""
    areas = area_registry.async_get(hass)
    floors = floor_registry.async_get(hass)
    for location in manifest.get("locations", []):
        area_name = _area_name(location)
        if not area_name:
            raise ConfigEntryError(
                f"Location {location.get('id', '<unknown>')} must define room or name"
            )
        floor_name = location.get("floor")
        floor = None
        if floor_name:
            floor = floors.async_get_floor_by_name(floor_name)
            configured_floor_icon = location.get("floor_icon")
            floor_icon = configured_floor_icon or _default_floor_icon(floor_name)
            floor_level = location.get("floor_level", _default_floor_level(floor_name))
            if floor is None:
                floor = floors.async_create(
                    floor_name,
                    icon=floor_icon,
                    level=floor_level,
                )
            else:
                floor_updates: dict[str, Any] = {}
                if floor_icon is not None and (
                    configured_floor_icon is not None
                    or floor.icon is None
                ) and floor.icon != floor_icon:
                    floor_updates["icon"] = floor_icon
                if floor_level is not None and floor.level != floor_level:
                    floor_updates["level"] = floor_level
                if floor_updates:
                    floors.async_update(floor.floor_id, **floor_updates)

        area = areas.async_get_area_by_name(area_name)
        if area is None:
            legacy_name = location.get("name")
            if legacy_name and legacy_name != area_name:
                area = areas.async_get_area_by_name(legacy_name)
                if area is not None:
                    area = areas.async_update(area.id, name=area_name)

        configured_area_icon = location.get("icon")
        area_icon = configured_area_icon or _default_area_icon(area_name)
        floor_id = floor.floor_id if floor is not None else None
        if area is None:
            areas.async_create(area_name, icon=area_icon, floor_id=floor_id)
        elif (
            (floor_id is not None and area.floor_id != floor_id)
            or (
                area_icon is not None
                and (configured_area_icon is not None or area.icon is None)
                and area.icon != area_icon
            )
        ):
            areas.async_update(
                area.id,
                floor_id=floor_id if floor_id is not None else area.floor_id,
                icon=(
                    area_icon
                    if area_icon is not None
                    and (configured_area_icon is not None or area.icon is None)
                    else area.icon
                ),
            )


def _area_ids(hass: HomeAssistant, manifest: dict[str, Any]) -> dict[str, str]:
    """Return location IDs mapped to Home Assistant area IDs."""
    registry = area_registry.async_get(hass)
    result: dict[str, str] = {}
    for location in manifest.get("locations", []):
        area_name = _area_name(location)
        area = registry.async_get_area_by_name(area_name)
        if area is not None:
            result[location["id"]] = area.id
    return result


def _area_name(location: dict[str, Any]) -> str | None:
    """Prefer the room name so floor prefixes remain floor metadata."""
    return location.get("room") or location.get("name")


def _default_floor_level(floor_name: str) -> int | None:
    """Map common German/English floor names to Home Assistant floor levels."""
    normalized_name = floor_name.strip().casefold()
    if normalized_name in {"eg", "ground", "ground floor", "erdgeschoss"}:
        return 0
    if normalized_name in {"og", "1. og", "1og", "first floor"}:
        return 1
    if normalized_name in {"2. og", "2og", "second floor"}:
        return 2
    if normalized_name in {"3. og", "3og", "third floor"}:
        return 3
    if normalized_name in {"ug", "keller", "basement", "lower ground"}:
        return -1
    if normalized_name in {"dg", "dachgeschoss", "attic"}:
        return 2
    return None


def _default_floor_icon(floor_name: str) -> str | None:
    """Select a familiar floor icon for common floor names."""
    normalized_name = floor_name.strip().casefold()
    if normalized_name in {"eg", "ground", "ground floor", "erdgeschoss"}:
        return "mdi:home-floor-0"
    if normalized_name in {"og", "1. og", "1og", "first floor"}:
        return "mdi:home-floor-1"
    if normalized_name in {"2. og", "2og", "second floor"}:
        return "mdi:home-floor-2"
    if normalized_name in {"3. og", "3og", "third floor"}:
        return "mdi:home-floor-3"
    if normalized_name in {"ug", "keller", "basement", "lower ground"}:
        return "mdi:home-floor-negative-1"
    if normalized_name in {"dg", "dachgeschoss", "attic"}:
        return "mdi:home-roof"
    return None


def _default_area_icon(area_name: str) -> str:
    """Choose a Material Design Icon from common German/English room names."""
    room_icons = (
        (("wohnzimmer", "living room", "lounge"), "mdi:sofa"),
        (("schlafzimmer", "bedroom", "guest room", "gaestezimmer"), "mdi:bed"),
        (("badezimmer", "bathroom", "bad", "shower"), "mdi:shower"),
        (("wc", "toilet", "toilette", "gaestetoilette"), "mdi:toilet"),
        (("kueche", "küche", "kitchen"), "mdi:chef-hat"),
        (("esszimmer", "dining room", "dining"), "mdi:table-chair"),
        (("arbeitszimmer", "buero", "office", "study"), "mdi:desk"),
        (("flur", "diele", "hallway", "corridor", "eingang"), "mdi:door"),
        (("kinderzimmer", "child room", "playroom", "spielzimmer"), "mdi:toy-brick"),
        (("waschkueche", "laundry", "waschraum"), "mdi:washing-machine"),
        (("garage", "carport"), "mdi:garage"),
        (("abstellraum", "storage", "lager", "vorratsraum"), "mdi:archive"),
        (("balkon", "terrasse", "patio"), "mdi:balcony"),
        (("garten", "garden"), "mdi:flower"),
        (("keller", "basement", "cellar"), "mdi:stairs-down"),
    )
    padded_words = f" {_normalize_icon_name(area_name)} "
    for names, icon in room_icons:
        for name in names:
            alias_words = _normalize_icon_name(name)
            if f" {alias_words} " in padded_words:
                return icon
    return "mdi:home"


def _normalize_icon_name(name: str) -> str:
    """Normalize names for matching German and English room keywords."""
    normalized_name = name.casefold()
    for source, replacement in (
        ("ä", "ae"),
        ("ö", "oe"),
        ("ü", "ue"),
        ("ß", "ss"),
    ):
        normalized_name = normalized_name.replace(source, replacement)
    return re.sub(r"[^a-z0-9]+", " ", normalized_name).strip()
