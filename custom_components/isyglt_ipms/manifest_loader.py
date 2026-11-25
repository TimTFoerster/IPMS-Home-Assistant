"""Load and validate IPMS manifests."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import yaml
from homeassistant.core import HomeAssistant
from homeassistant.exceptions import ConfigEntryError

from .const import LIGHT_MODES, NUMERIC_MODES, PLATFORMS

_LIGHT_MODES = set(LIGHT_MODES)
_NUMERIC_MODES = set(NUMERIC_MODES)
_PLATFORMS = set(PLATFORMS)
_PLATFORM_MODES = {
    "light": _LIGHT_MODES,
    "number": _NUMERIC_MODES,
    "sensor": _NUMERIC_MODES,
}
_REQUIRED_MARKERS = {
    "switch": ("state_marker",),
    "binary_sensor": ("state_marker",),
    "button": ("command_marker",),
    "number": ("marker",),
    "climate": ("current_temp_marker", "target_temp_marker"),
}
_LIGHT_MARKERS = {
    "bit": ("state_marker",),
    "dimmer": ("brightness_marker",),
    "dimmer_tw": ("brightness_marker", "color_temp_marker"),
    "rgb": ("red_marker", "green_marker", "blue_marker"),
    "rgbw": ("red_marker", "green_marker", "blue_marker", "white_marker"),
}


async def async_load_manifest(
    hass: HomeAssistant, path: Path
) -> dict[str, Any]:
    """Load a YAML/JSON manifest off the event loop and validate it."""
    try:
        manifest = await hass.async_add_executor_job(_load_manifest, path)
    except (OSError, UnicodeError, ValueError, yaml.YAMLError) as err:
        raise ConfigEntryError(f"Unable to read manifest {path}: {err}") from err
    _validate_manifest(manifest)
    return manifest


def _load_manifest(path: Path) -> Any:
    """Read a manifest from disk."""
    content = path.read_text(encoding="utf-8")
    if path.suffix.lower() == ".json":
        return json.loads(content)
    if path.suffix.lower() in (".yaml", ".yml"):
        return yaml.safe_load(content)
    raise ValueError(f"Unsupported manifest extension: {path.suffix}")


def _validate_manifest(manifest: Any) -> None:
    """Validate required manifest fields and entity platform/mode values."""
    if not isinstance(manifest, dict):
        raise ConfigEntryError("Manifest must contain a YAML/JSON object")

    missing = [key for key in ("schema_version", "master", "entities") if key not in manifest]
    if missing:
        raise ConfigEntryError(f"Manifest is missing required fields: {', '.join(missing)}")
    if (
        not isinstance(manifest["schema_version"], int)
        or isinstance(manifest["schema_version"], bool)
        or manifest["schema_version"] != 1
    ):
        raise ConfigEntryError(
            f"Unsupported schema_version {manifest['schema_version']!r}; expected 1"
        )
    if not isinstance(manifest["master"], dict):
        raise ConfigEntryError("Manifest field 'master' must be an object")
    if not isinstance(manifest["entities"], list):
        raise ConfigEntryError("Manifest field 'entities' must be a list")
    if "locations" in manifest and not isinstance(manifest["locations"], list):
        raise ConfigEntryError("Manifest field 'locations' must be a list")
    location_ids: set[str] = set()
    for index, location in enumerate(manifest.get("locations", [])):
        if not isinstance(location, dict) or not isinstance(location.get("id"), str):
            raise ConfigEntryError(
                f"Location at index {index} must be an object with a string id"
            )
        if location["id"] in location_ids:
            raise ConfigEntryError(
                f"Manifest contains duplicate location id {location['id']!r}"
            )
        location_ids.add(location["id"])
        if not (location.get("name") or location.get("room")):
            raise ConfigEntryError(
                f"Location {location['id']!r} must define name or room"
            )

    entity_ids: set[str] = set()
    for index, entity in enumerate(manifest["entities"]):
        if not isinstance(entity, dict):
            raise ConfigEntryError(f"Entity at index {index} must be an object")
        if not isinstance(entity.get("id"), str):
            raise ConfigEntryError(f"Entity at index {index} must define a string id")
        if entity["id"] in entity_ids:
            raise ConfigEntryError(
                f"Manifest contains duplicate entity id {entity['id']!r}"
            )
        entity_ids.add(entity["id"])
        platform = entity.get("platform")
        if not isinstance(platform, str) or platform not in _PLATFORMS:
            raise ConfigEntryError(
                f"Entity at index {index} has unsupported platform {platform!r}; "
                f"expected one of {', '.join(sorted(_PLATFORMS))}"
            )
        mode = entity.get("mode")
        valid_modes = _PLATFORM_MODES.get(platform)
        if mode is not None and (
            not isinstance(mode, str)
            or valid_modes is None
            or mode not in valid_modes
        ):
            expected_modes = (
                ", ".join(sorted(valid_modes))
                if valid_modes is not None
                else "no mode is supported"
            )
            raise ConfigEntryError(
                f"Entity {entity.get('id', index)!r} has invalid mode {mode!r} "
                f"for platform {platform!r}; expected {expected_modes}"
            )
        if platform == "light" and mode is None:
            raise ConfigEntryError(
                f"Entity {entity.get('id', index)!r} requires a light mode"
            )
        if platform == "light":
            if not isinstance(mode, str) or mode not in _LIGHT_MARKERS:
                raise ConfigEntryError(
                    f"Entity {entity['id']!r} requires a valid light mode"
                )
            required_markers = _LIGHT_MARKERS[mode]
        else:
            required_markers = _REQUIRED_MARKERS.get(platform, ())
        missing_markers = [
            marker for marker in required_markers if not entity.get(marker)
        ]
        if missing_markers:
            raise ConfigEntryError(
                f"Entity {entity['id']!r} is missing required marker fields: "
                f"{', '.join(missing_markers)}"
            )
        if platform == "cover" and not all(
            entity.get(marker) for marker in ("up_marker", "down_marker")
        ):
            raise ConfigEntryError(
                f"Cover {entity['id']!r} requires up_marker and down_marker"
            )
        if platform == "sensor":
            paired_markers = (entity.get("high_marker"), entity.get("low_marker"))
            if any(paired_markers) and not all(paired_markers):
                raise ConfigEntryError(
                    f"Sensor {entity['id']!r} must define both high_marker and low_marker"
                )
            if not all(paired_markers) and not entity.get("state_marker"):
                raise ConfigEntryError(
                    f"Sensor {entity['id']!r} requires state_marker or both "
                    "high_marker and low_marker"
                )
        for field, marker in entity.items():
            if field.endswith("_marker") and marker is not None and (
                not isinstance(marker, str) or not marker
            ):
                raise ConfigEntryError(
                    f"Entity {entity['id']!r} field {field!r} must be a non-empty "
                    "string marker"
                )
        for field in ("scale", "offset", "temperature_scale"):
            value = entity.get(field)
            if value is not None and (
                isinstance(value, bool) or not isinstance(value, (int, float))
            ):
                raise ConfigEntryError(
                    f"Entity {entity['id']!r} field {field!r} must be numeric"
                )
        location_id = entity.get("location_id")
        if location_id is not None and (
            not isinstance(location_id, str) or location_id not in location_ids
        ):
            raise ConfigEntryError(
                f"Entity {entity['id']!r} references unknown location_id "
                f"{location_id!r}"
            )
        if entity.get("scale", 1) == 0 or entity.get("temperature_scale", 1.0) <= 0:
            raise ConfigEntryError(
                f"Entity {entity['id']!r} scale must not be zero"
            )
        if platform == "number":
            default_minimum, default_maximum = {
                "byte": (0, 255),
                "signed_byte": (-128, 127),
                "word": (0, 65535),
                "signed_word": (-32768, 32767),
            }[entity.get("mode", "byte")]
            minimum = entity.get("min_value", default_minimum)
            maximum = entity.get("max_value", default_maximum)
            step = entity.get("step", 1)
            if any(
                isinstance(value, bool) or not isinstance(value, (int, float))
                for value in (minimum, maximum, step)
            ) or minimum >= maximum or step <= 0:
                raise ConfigEntryError(
                    f"Number {entity['id']!r} requires min_value < max_value "
                    "and a positive numeric step"
                )
        if platform == "light" and mode == "dimmer_tw":
            minimum = entity.get("min_kelvin", 2700)
            maximum = entity.get("max_kelvin", 6500)
            if (
                not isinstance(minimum, (int, float))
                or not isinstance(maximum, (int, float))
                or isinstance(minimum, bool)
                or isinstance(maximum, bool)
                or minimum >= maximum
            ):
                raise ConfigEntryError(
                    f"Light {entity['id']!r} requires min_kelvin below max_kelvin"
                )
