"""Shared entity behavior for IPMS entities."""

from __future__ import annotations

from typing import Any

from homeassistant.helpers import device_registry as dr
from homeassistant.helpers import entity_registry as er
from homeassistant.helpers.update_coordinator import CoordinatorEntity

from .const import CONF_IPMS_URL, DOMAIN
from .coordinator import IsygltDataUpdateCoordinator


class IsygltEntity(CoordinatorEntity[IsygltDataUpdateCoordinator]):
    """Base class handling identity, room device, and enable/fault markers."""

    _attr_has_entity_name = True

    def __init__(
        self,
        coordinator: IsygltDataUpdateCoordinator,
        entity_config: dict[str, Any],
        area_ids: dict[str, str],
    ) -> None:
        super().__init__(coordinator)
        self.entity_config = entity_config
        self._area_ids = area_ids
        self._attr_unique_id = (
            f"{coordinator.master.get(CONF_IPMS_URL, '')}|"
            f"{coordinator.master.get('alias', '')}|{entity_config['id']}"
        )
        self._attr_name = entity_config.get("name")
        location_id = entity_config.get("location_id")
        location = next(
            (
                item
                for item in coordinator.manifest.get("locations", [])
                if item.get("id") == location_id
            ),
            None,
        )
        location_name = (location or {}).get("room") or (location or {}).get("name")
        master_alias = coordinator.master.get("alias", "")
        self._device_identifier = (
            DOMAIN,
            f"{coordinator.master.get(CONF_IPMS_URL, '')}|"
            f"{master_alias}|{location_id or 'master'}",
        )
        self._attr_device_info = dr.DeviceInfo(
            identifiers={self._device_identifier},
            name=(
                f"{master_alias} - {location_name}"
                if location_name
                else f"ISYGLT {master_alias}"
            ),
            manufacturer="ISYGLT",
            model="IPMS",
        )
        self._location_id = location_id

    @property
    def available(self) -> bool:
        """Report unavailable for offline, disabled, or faulty entities."""
        return super().available and self._lock_reason is None

    @property
    def _lock_reason(self) -> str | None:
        if not self.coordinator.last_update_success:
            return "offline"
        if not self.coordinator.master_online:
            return "master_offline"
        enable_marker = self.entity_config.get("enable_marker")
        if enable_marker and self.coordinator.get_bit(enable_marker) is False:
            return "disabled"
        fault_marker = self.entity_config.get("fault_marker")
        if fault_marker and self.coordinator.get_bit(fault_marker) is True:
            return "fault"
        return None

    @property
    def extra_state_attributes(self) -> dict[str, Any]:
        """Expose the reason an entity is unavailable."""
        reason = self._lock_reason
        return {"lock_reason": reason} if reason is not None else {}

    async def async_added_to_hass(self) -> None:
        """Assign this entity's device and registry entry to its room area."""
        await super().async_added_to_hass()
        if not isinstance(self._location_id, str):
            return
        area_id = self._area_ids.get(self._location_id)
        if area_id is None:
            return

        device_registry = dr.async_get(self.hass)
        device = device_registry.async_get_device(
            identifiers={self._device_identifier}
        )
        if device is not None and device.area_id != area_id:
            device_registry.async_update_device(device.id, area_id=area_id)

        entity_registry = er.async_get(self.hass)
        entity_entry = entity_registry.async_get(self.entity_id)
        if entity_entry is not None and entity_entry.area_id != area_id:
            entity_registry.async_update_entity(self.entity_id, area_id=area_id)
