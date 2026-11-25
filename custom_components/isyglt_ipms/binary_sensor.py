"""Binary sensor platform for ISYGLT IPMS."""

from __future__ import annotations

from typing import Any

from homeassistant.components.binary_sensor import (
    BinarySensorDeviceClass,
    BinarySensorEntity,
)
from homeassistant.config_entries import ConfigEntry
from homeassistant.const import EntityCategory
from homeassistant.core import HomeAssistant
from homeassistant.helpers.device_registry import DeviceInfo
from homeassistant.helpers.entity_platform import AddEntitiesCallback
from homeassistant.helpers.update_coordinator import CoordinatorEntity

from .const import CONF_IPMS_URL, DOMAIN
from .entity import IsygltEntity


async def async_setup_entry(
    hass: HomeAssistant,
    entry: ConfigEntry,
    async_add_entities: AddEntitiesCallback,
) -> None:
    """Set up configured binary sensors and the master diagnostic."""
    data = hass.data[DOMAIN][entry.entry_id]
    entities: list[BinarySensorEntity] = [
        IsygltBinarySensor(data["coordinator"], config, data["areas"])
        for config in data["manifest"]["entities"]
        if config["platform"] == "binary_sensor"
    ]
    entities.append(IsygltMasterConnectivitySensor(data["coordinator"]))
    async_add_entities(entities)


class IsygltBinarySensor(IsygltEntity, BinarySensorEntity):
    """An IPMS bit binary sensor."""

    def __init__(self, coordinator: Any, config: dict[str, Any], areas: dict[str, str]) -> None:
        super().__init__(coordinator, config, areas)
        device_class = config.get("device_class")
        self._attr_device_class = (
            BinarySensorDeviceClass(device_class) if device_class is not None else None
        )

    @property
    def is_on(self) -> bool | None:
        """Return the state bit."""
        return self.coordinator.get_bit(self.entity_config["state_marker"])


class IsygltMasterConnectivitySensor(
    CoordinatorEntity[Any], BinarySensorEntity
):
    """Connectivity diagnostic for this IPMS master."""

    _attr_device_class = BinarySensorDeviceClass.CONNECTIVITY
    _attr_entity_category = EntityCategory.DIAGNOSTIC
    _attr_has_entity_name = True
    _attr_name = "Connectivity"

    def __init__(self, coordinator: Any) -> None:
        super().__init__(coordinator)
        alias = coordinator.master.get("alias", "")
        self._attr_unique_id = (
            f"{coordinator.master.get(CONF_IPMS_URL, '')}|{alias}|connectivity"
        )
        self._attr_device_info = DeviceInfo(
            identifiers={
                (
                    DOMAIN,
                    f"{coordinator.master.get(CONF_IPMS_URL, '')}|{alias}|master",
                )
            },
            name=f"ISYGLT {alias}",
            manufacturer="ISYGLT",
            model="IPMS",
        )

    @property
    def available(self) -> bool:
        """Remain available to report the disconnected state."""
        return True

    @property
    def is_on(self) -> bool:
        """Return the coordinator's master-cycle watchdog state."""
        return self.coordinator.master_online
