from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
from datetime import datetime, timezone
import math
from typing import Any, Literal


LocationQuality = Literal["valid", "last_known"]


@dataclass(frozen=True)
class VehicleLocation:
    """A validated vehicle location and non-sensitive quality metadata."""

    latitude: float
    longitude: float
    quality: LocationQuality
    valid: bool
    stale: bool
    gps_privacy_switch: int | None = None
    satellite_count: int | None = None
    cloud_location_observed_at: datetime | None = None
    cloud_payload_updated_at: datetime | None = None

    @property
    def coordinates(self) -> tuple[float, float]:
        return self.latitude, self.longitude


def parse_vehicle_location(data: Any) -> VehicleLocation | None:
    """Return usable coordinates without claiming invalid cloud data is current."""
    if not isinstance(data, dict):
        return None
    section = data.get("location")
    if not isinstance(section, dict):
        return None
    coordinates = section.get("location")
    if not isinstance(coordinates, dict):
        return None

    latitude = _coordinate(coordinates.get("latitude"), minimum=-90, maximum=90)
    longitude = _coordinate(coordinates.get("longitude"), minimum=-180, maximum=180)
    if latitude is None or longitude is None or (latitude == 0 and longitude == 0):
        return None

    valid = _valid_flag(coordinates.get("validFlag"))
    return VehicleLocation(
        latitude=latitude,
        longitude=longitude,
        quality="valid" if valid else "last_known",
        valid=valid,
        stale=not valid,
        gps_privacy_switch=_binary_flag(
            coordinates.get("gpsPrivacySwitch", section.get("gpsPrivacySwitch"))
        ),
        satellite_count=_first_nonnegative_integer(
            section.get("sateliteNum"),
            section.get("satelliteNum"),
            section.get("satelliteCount"),
            coordinates.get("gpsSatelliteNum"),
            coordinates.get("sateliteNum"),
        ),
        # These cloud fields do not reliably match the App's displayed fix time,
        # so expose them as observation/payload timestamps rather than GPS fixes.
        cloud_location_observed_at=_cloud_timestamp(section.get("gpsTime")),
        cloud_payload_updated_at=_cloud_timestamp(
            section.get("lastUpdatedAt") or data.get("lastUpdatedAt")
        ),
    )


def location_attributes(
    reading: VehicleLocation,
    *,
    as_local: Callable[[datetime], datetime],
) -> dict[str, Any]:
    """Build metadata attributes without duplicating coordinates or identifiers."""
    attributes: dict[str, Any] = {
        "location_quality": reading.quality,
        "location_valid": reading.valid,
        "location_stale": reading.stale,
    }
    if reading.gps_privacy_switch is not None:
        attributes["gps_privacy_switch"] = reading.gps_privacy_switch
    if reading.satellite_count is not None:
        attributes["gps_satellite_count"] = reading.satellite_count
    if reading.cloud_location_observed_at is not None:
        attributes["cloud_location_observed_at"] = as_local(
            reading.cloud_location_observed_at
        ).isoformat()
    if reading.cloud_payload_updated_at is not None:
        attributes["cloud_payload_updated_at"] = as_local(
            reading.cloud_payload_updated_at
        ).isoformat()
    return attributes


def _coordinate(value: Any, *, minimum: float, maximum: float) -> float | None:
    if not isinstance(value, (int, float)) or isinstance(value, bool):
        return None
    numeric = float(value)
    if not math.isfinite(numeric) or not minimum <= numeric <= maximum:
        return None
    return numeric


def _valid_flag(value: Any) -> bool:
    return not isinstance(value, bool) and value in {1, "1"}


def _binary_flag(value: Any) -> int | None:
    if isinstance(value, bool):
        return None
    if value in {0, "0"}:
        return 0
    if value in {1, "1"}:
        return 1
    return None


def _first_nonnegative_integer(*values: Any) -> int | None:
    for value in values:
        if isinstance(value, bool):
            continue
        try:
            integer = int(value)
        except (TypeError, ValueError, OverflowError):
            continue
        if integer >= 0:
            return integer
    return None


def _cloud_timestamp(value: Any) -> datetime | None:
    if not isinstance(value, (int, float)) or isinstance(value, bool) or value <= 0:
        return None
    seconds = value / 1000 if value > 10_000_000_000 else value
    try:
        return datetime.fromtimestamp(seconds, tz=timezone.utc)
    except (OSError, OverflowError, ValueError):
        return None
