"""Constants for the ISYGLT IPMS integration."""

from typing import Final

DOMAIN: Final = "isyglt_ipms"

PLATFORMS: Final = (
    "light",
    "switch",
    "cover",
    "button",
    "number",
    "climate",
    "sensor",
    "binary_sensor",
)

LIGHT_MODES: Final = ("bit", "dimmer", "dimmer_tw", "rgb", "rgbw")
NUMERIC_MODES: Final = ("byte", "signed_byte", "word", "signed_word")

DEFAULT_SCAN_INTERVAL: Final = 1.0
DEFAULT_PULSE_MS: Final = 150

CONF_IPMS_URL: Final = "ipms_url"
CONF_MASTER_ALIAS: Final = "master_alias"
CONF_MANIFEST_PATH: Final = "manifest_path"
CONF_SCAN_INTERVAL: Final = "scan_interval"
CONF_PULSE_MS: Final = "pulse_ms"
