from __future__ import annotations

from homeassistant.components.device_tracker.config_entry import TrackerEntity
from homeassistant.components.device_tracker.const import SourceType
from homeassistant.helpers.update_coordinator import CoordinatorEntity
from homeassistant.util import dt as dt_util

from .const import DOMAIN
from .coordinator import AitoDataCoordinator
from .location import VehicleLocation, location_attributes, parse_vehicle_location
from .models import Vehicle, vehicle_device_info


async def async_setup_entry(hass, entry, async_add_entities) -> None:
    data = hass.data[DOMAIN][entry.entry_id]
    coordinator = data.get("coordinator")
    if coordinator is None:
        return
    async_add_entities(
        AitoVehicleLocationTracker(coordinator, vehicle)
        for vehicle in data["vehicles"]
        if (spec := data["vehicle_specs"].get(vehicle.id)) and spec.supports_location
    )


class AitoVehicleLocationTracker(CoordinatorEntity[AitoDataCoordinator], TrackerEntity):
    """Expose valid or clearly marked last-known vehicle coordinates."""

    _attr_has_entity_name = True
    _attr_translation_key = "location"

    def __init__(self, coordinator: AitoDataCoordinator, vehicle: Vehicle) -> None:
        super().__init__(coordinator)
        self._vehicle_id = vehicle.id
        self._attr_unique_id = f"{vehicle.id}_location"
        self._attr_device_info = vehicle_device_info(vehicle)

    @property
    def available(self) -> bool:
        return super().available and self._location is not None

    @property
    def source_type(self) -> SourceType:
        return SourceType.GPS

    @property
    def latitude(self) -> float | None:
        location = self._location
        return location.latitude if location is not None else None

    @property
    def longitude(self) -> float | None:
        location = self._location
        return location.longitude if location is not None else None

    @property
    def extra_state_attributes(self) -> dict | None:
        location = self._location
        if location is None:
            return None
        return location_attributes(location, as_local=dt_util.as_local)

    @property
    def _location(self) -> VehicleLocation | None:
        data = self.coordinator.data.get(self._vehicle_id, {}) if self.coordinator.data else {}
        return parse_vehicle_location(data)
