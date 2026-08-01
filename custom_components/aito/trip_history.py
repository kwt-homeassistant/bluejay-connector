from __future__ import annotations

from dataclasses import dataclass, replace
from datetime import date, datetime, timezone, tzinfo
import math
from typing import Any, Mapping


_SAFE_SHAPE_KEYS = {
    "avgFuelConsum",
    "avgPowerConsum",
    "avgSpeed",
    "code",
    "data",
    "date",
    "endDate",
    "errorCode",
    "maxSpeed",
    "message",
    "msg",
    "odo",
    "result",
    "resultCode",
    "startDate",
    "tripList",
    "tripOdo",
    "trips",
    "tripStartTime",
    "tripTime",
}


@dataclass(frozen=True)
class TripRecord:
    distance_km: float | None
    started_at: datetime | None
    duration_minutes: int | None
    average_speed_kmh: float | None
    max_speed_kmh: float | None
    average_power_consumption: float | None
    average_fuel_consumption: float | None


@dataclass(frozen=True)
class TripDay:
    day: date
    distance_km: float | None
    trips: tuple[TripRecord, ...]

    @property
    def effective_distance_km(self) -> float:
        if self.distance_km is not None:
            return self.distance_km
        return sum(trip.distance_km or 0 for trip in self.trips)


@dataclass(frozen=True)
class TripHistory:
    start_date: date
    end_date: date
    fetched_at: datetime
    days: tuple[TripDay, ...]
    coverage_ranges: tuple[tuple[date, date], ...]

    @property
    def latest_trip(self) -> TripRecord | None:
        trips = [
            trip
            for day in self.days
            for trip in day.trips
            if trip.started_at is not None
        ]
        return max(trips, key=lambda trip: trip.started_at) if trips else None

    def sensor_data(self, today: date) -> dict[str, Any]:
        latest = self.latest_trip
        recent_start = today - date.resolution * 6
        today_distance = sum(
            day.effective_distance_km for day in self.days if day.day == today
        )
        recent_distance = sum(
            day.effective_distance_km
            for day in self.days
            if recent_start <= day.day <= today
        )
        retention_start = today - date.resolution * 364
        coverage_days = _coverage_days(
            self.coverage_ranges,
            retention_start,
            today,
        )
        backfill_progress = (
            100
            if coverage_days >= 365
            else round(coverage_days * 100 / 365, 1)
        )
        return {
            "lastTripOdo": latest.distance_km if latest is not None else None,
            "lastTripStartTime": latest.started_at if latest is not None else None,
            "lastTripTime": latest.duration_minutes if latest is not None else None,
            "todayOdo": round(today_distance, 3),
            "recentSevenDayOdo": round(recent_distance, 3),
            "updatedAt": self.fetched_at,
            "archiveCoverageDays": coverage_days,
            "archiveBackfillProgress": backfill_progress,
        }

    def to_storage(self) -> dict[str, Any]:
        return {
            "startDate": self.start_date.isoformat(),
            "endDate": self.end_date.isoformat(),
            "fetchedAt": self.fetched_at.isoformat(),
            "coverageRanges": [
                {"startDate": start.isoformat(), "endDate": end.isoformat()}
                for start, end in self.coverage_ranges
            ],
            "days": [
                {
                    "date": day.day.isoformat(),
                    "odo": day.distance_km,
                    "trips": [
                        {
                            "tripOdo": trip.distance_km,
                            "tripStartTime": trip.started_at.isoformat()
                            if trip.started_at is not None
                            else None,
                            "tripTime": trip.duration_minutes,
                            "avgSpeed": trip.average_speed_kmh,
                            "maxSpeed": trip.max_speed_kmh,
                            "avgPowerConsum": trip.average_power_consumption,
                            "avgFuelConsum": trip.average_fuel_consumption,
                        }
                        for trip in day.trips
                    ],
                }
                for day in self.days
            ],
        }

    @classmethod
    def from_storage(cls, data: Any) -> "TripHistory":
        if not isinstance(data, Mapping):
            raise ValueError("saved AITO trip history must be an object")
        try:
            start_date = date.fromisoformat(str(data["startDate"]))
            end_date = date.fromisoformat(str(data["endDate"]))
        except (KeyError, ValueError) as error:
            raise ValueError("saved AITO trip history dates are invalid") from error
        fetched_at = _timestamp_value(data.get("fetchedAt"))
        if fetched_at is None:
            raise ValueError("saved AITO trip history timestamp is invalid")
        history = cls.from_api(
            {"trips": data.get("days")},
            start_date=start_date,
            end_date=end_date,
            fetched_at=fetched_at,
        )
        coverage_ranges = _stored_coverage_ranges(
            data.get("coverageRanges"),
            fallback=(start_date, end_date),
        )
        return replace(history, coverage_ranges=coverage_ranges)

    @classmethod
    def merge(
        cls,
        *histories: "TripHistory",
        retention_start: date,
        retention_end: date,
    ) -> "TripHistory":
        if not histories:
            raise ValueError("at least one trip history is required")
        day_by_date = {
            day.day: day
            for history in histories
            for day in history.days
            if retention_start <= day.day <= retention_end
        }
        coverage_ranges = _normalize_coverage_ranges(
            range_value
            for history in histories
            for range_value in history.coverage_ranges
        )
        coverage_ranges = _trim_coverage_ranges(
            coverage_ranges,
            retention_start,
            retention_end,
        )
        if not coverage_ranges:
            raise ValueError("trip history has no retained coverage")
        return cls(
            start_date=coverage_ranges[0][0],
            end_date=coverage_ranges[-1][1],
            fetched_at=max(history.fetched_at for history in histories),
            days=tuple(
                sorted(day_by_date.values(), key=lambda item: item.day, reverse=True)
            ),
            coverage_ranges=coverage_ranges,
        )

    @classmethod
    def from_api(
        cls,
        response: Any,
        *,
        start_date: date,
        end_date: date,
        fetched_at: datetime,
        local_tz: tzinfo = timezone.utc,
    ) -> "TripHistory":
        if end_date < start_date:
            raise ValueError("trip history end date is before its start date")
        groups = _trip_groups(response)
        days: list[TripDay] = []
        for group in groups:
            trips = tuple(
                trip
                for item in _mapping_list(group.get("trips"))
                if (trip := _trip_record(item)) is not None
            )
            day = _day_value(group.get("date"), local_tz)
            if day is None:
                started_days = [
                    trip.started_at.astimezone(local_tz).date()
                    for trip in trips
                    if trip.started_at is not None
                ]
                day = max(started_days) if started_days else None
            if day is None or day < start_date or day > end_date:
                continue
            days.append(
                TripDay(
                    day=day,
                    distance_km=_non_negative_float(group.get("odo")),
                    trips=tuple(
                        sorted(
                            trips,
                            key=lambda trip: trip.started_at
                            or datetime.min.replace(tzinfo=timezone.utc),
                            reverse=True,
                        )
                    ),
                )
            )
        return cls(
            start_date=start_date,
            end_date=end_date,
            fetched_at=_aware_utc(fetched_at),
            days=tuple(sorted(days, key=lambda item: item.day, reverse=True)),
            coverage_ranges=((start_date, end_date),),
        )


def next_backfill_range(
    history: TripHistory | None,
    *,
    today: date,
    retention_days: int = 365,
    chunk_days: int = 30,
) -> tuple[date, date] | None:
    if retention_days < 1 or chunk_days < 1:
        raise ValueError("trip history retention and chunk days must be positive")
    retention_start = today - date.resolution * (retention_days - 1)
    ranges = _trim_coverage_ranges(
        history.coverage_ranges if history is not None else (),
        retention_start,
        today,
    )
    cursor = today
    for start, end in reversed(ranges):
        if start > cursor:
            continue
        if end < cursor:
            gap_start = end + date.resolution
            return (
                max(
                    retention_start,
                    gap_start,
                    cursor - date.resolution * (chunk_days - 1),
                ),
                cursor,
            )
        cursor = start - date.resolution
        if cursor < retention_start:
            return None
    if cursor < retention_start:
        return None
    return max(retention_start, cursor - date.resolution * (chunk_days - 1)), cursor


def _trip_groups(response: Any) -> list[Mapping[str, Any]]:
    if isinstance(response, list):
        return _mapping_list(response)
    candidates = [response]
    if isinstance(response, Mapping):
        candidates.extend(response.get(key) for key in ("data", "result"))
    for candidate in candidates:
        if isinstance(candidate, list):
            return _mapping_list(candidate)
        if isinstance(candidate, Mapping):
            for key in ("trips", "tripList"):
                if isinstance(candidate.get(key), list):
                    return _mapping_list(candidate[key])
    raise ValueError("AITO trip history response does not contain a trips list")


def response_shape(response: Any) -> str:
    """Describe only container types and safe field names, never response values."""

    def describe(value: Any, depth: int) -> str:
        if isinstance(value, Mapping):
            if depth >= 4:
                return "object"
            fields: list[str] = []
            for raw_key, item in list(value.items())[:24]:
                key = str(raw_key)
                safe_key = key if key in _SAFE_SHAPE_KEYS else "<redacted-key>"
                fields.append(f"{safe_key}:{describe(item, depth + 1)}")
            return "object{" + ",".join(fields) + "}"
        if isinstance(value, list):
            if depth >= 4:
                return "list"
            return "list" if not value else f"list[{describe(value[0], depth + 1)}]"
        if value is None:
            return "null"
        if isinstance(value, bool):
            return "bool"
        if isinstance(value, (int, float)):
            return "number"
        if isinstance(value, str):
            return "string"
        return type(value).__name__

    return describe(response, 0)


def _trip_record(data: Mapping[str, Any]) -> TripRecord | None:
    started_at = _timestamp_value(data.get("tripStartTime"))
    distance = _non_negative_float(data.get("tripOdo"))
    duration = _non_negative_int(data.get("tripTime"))
    if started_at is None and distance is None and duration is None:
        return None
    return TripRecord(
        distance_km=distance,
        started_at=started_at,
        duration_minutes=duration,
        average_speed_kmh=_non_negative_float(data.get("avgSpeed")),
        max_speed_kmh=_non_negative_float(data.get("maxSpeed")),
        average_power_consumption=_non_negative_float(data.get("avgPowerConsum")),
        average_fuel_consumption=_non_negative_float(data.get("avgFuelConsum")),
    )


def _mapping_list(value: Any) -> list[Mapping[str, Any]]:
    if not isinstance(value, list):
        return []
    return [item for item in value if isinstance(item, Mapping)]


def _non_negative_float(value: Any) -> float | None:
    if isinstance(value, bool) or value is None:
        return None
    try:
        number = float(value)
    except (TypeError, ValueError):
        return None
    return number if math.isfinite(number) and number >= 0 else None


def _non_negative_int(value: Any) -> int | None:
    number = _non_negative_float(value)
    return int(number) if number is not None else None


def _timestamp_value(value: Any) -> datetime | None:
    if isinstance(value, str) and not value.strip().replace(".", "", 1).isdigit():
        try:
            parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
        except ValueError:
            return None
        return _aware_utc(parsed)
    number = _non_negative_float(value)
    if number is None or number == 0:
        return None
    seconds = number / 1000 if number > 10_000_000_000 else number
    try:
        return datetime.fromtimestamp(seconds, tz=timezone.utc)
    except (OverflowError, OSError, ValueError):
        return None


def _day_value(value: Any, local_tz: tzinfo) -> date | None:
    if isinstance(value, str):
        try:
            return date.fromisoformat(value[:10])
        except ValueError:
            pass
    timestamp = _timestamp_value(value)
    return timestamp.astimezone(local_tz).date() if timestamp is not None else None


def _aware_utc(value: datetime) -> datetime:
    if value.tzinfo is None:
        return value.replace(tzinfo=timezone.utc)
    return value.astimezone(timezone.utc)


def _stored_coverage_ranges(
    value: Any,
    *,
    fallback: tuple[date, date],
) -> tuple[tuple[date, date], ...]:
    ranges: list[tuple[date, date]] = []
    if isinstance(value, list):
        for item in value:
            if not isinstance(item, Mapping):
                continue
            try:
                start = date.fromisoformat(str(item.get("startDate")))
                end = date.fromisoformat(str(item.get("endDate")))
            except ValueError:
                continue
            if start <= end:
                ranges.append((start, end))
    return _normalize_coverage_ranges(ranges or (fallback,))


def _normalize_coverage_ranges(
    ranges: Any,
) -> tuple[tuple[date, date], ...]:
    normalized: list[tuple[date, date]] = []
    for start, end in sorted(ranges):
        if start > end:
            continue
        if normalized and start <= normalized[-1][1] + date.resolution:
            normalized[-1] = (normalized[-1][0], max(normalized[-1][1], end))
        else:
            normalized.append((start, end))
    return tuple(normalized)


def _trim_coverage_ranges(
    ranges: Any,
    retention_start: date,
    retention_end: date,
) -> tuple[tuple[date, date], ...]:
    return _normalize_coverage_ranges(
        (
            max(start, retention_start),
            min(end, retention_end),
        )
        for start, end in ranges
        if end >= retention_start and start <= retention_end
    )


def _coverage_days(
    ranges: Any,
    retention_start: date,
    retention_end: date,
) -> int:
    return sum(
        (end - start).days + 1
        for start, end in _trim_coverage_ranges(ranges, retention_start, retention_end)
    )
