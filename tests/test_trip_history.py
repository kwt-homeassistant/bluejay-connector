from __future__ import annotations

from datetime import date, datetime, timedelta, timezone
import importlib.util
import json
from pathlib import Path
import sys
import types
import unittest


ROOT = Path(__file__).resolve().parents[1]
PACKAGE_ROOT = ROOT / "custom_components" / "aito"
LOCAL_TZ = timezone(timedelta(hours=8))


def _load_module(name: str, path: Path):
    spec = importlib.util.spec_from_file_location(name, path)
    if spec is None or spec.loader is None:
        raise RuntimeError(f"Cannot load {name}")
    module = importlib.util.module_from_spec(spec)
    sys.modules[name] = module
    spec.loader.exec_module(module)
    return module


custom_components = types.ModuleType("custom_components")
custom_components.__path__ = [str(ROOT / "custom_components")]
sys.modules.setdefault("custom_components", custom_components)

aito = types.ModuleType("custom_components.aito")
aito.__path__ = [str(PACKAGE_ROOT)]
sys.modules.setdefault("custom_components.aito", aito)

trip_history = _load_module(
    "custom_components.aito.trip_history",
    PACKAGE_ROOT / "trip_history.py",
)


class TripHistoryTest(unittest.TestCase):
    def test_response_shape_never_includes_values_or_unknown_keys(self) -> None:
        shape = trip_history.response_shape(
            {
                "resultCode": 0,
                "data": {
                    "tripList": [
                        {
                            "tripOdo": 12.3,
                            "tripId": "private-trip-id",
                            "2026-08-01": {"coordinate": "private-location"},
                        }
                    ]
                },
            }
        )

        self.assertIn("resultCode:number", shape)
        self.assertIn("tripList:list[object", shape)
        self.assertIn("<redacted-key>:object", shape)
        self.assertNotIn("12.3", shape)
        self.assertNotIn("private-trip-id", shape)
        self.assertNotIn("private-location", shape)

    def test_parses_native_top_level_trip_array(self) -> None:
        history = trip_history.TripHistory.from_api(
            [
                {
                    "date": "2099-01-07",
                    "odo": 7.5,
                    "trips": [
                        {
                            "tripOdo": 7.5,
                            "tripStartTime": "2099-01-07T10:00:00+08:00",
                            "tripTime": 20,
                        }
                    ],
                }
            ],
            start_date=date(2099, 1, 1),
            end_date=date(2099, 1, 7),
            fetched_at=datetime(2099, 1, 7, 3, tzinfo=timezone.utc),
            local_tz=LOCAL_TZ,
        )

        self.assertEqual(history.days[0].distance_km, 7.5)
        self.assertEqual(history.latest_trip.duration_minutes, 20)

    def test_parses_wrapped_trip_list_compatibility_shape(self) -> None:
        history = trip_history.TripHistory.from_api(
            {"data": {"tripList": [{"date": "2099-01-07", "odo": 2, "trips": []}]}},
            start_date=date(2099, 1, 1),
            end_date=date(2099, 1, 7),
            fetched_at=datetime(2099, 1, 7, 3, tzinfo=timezone.utc),
            local_tz=LOCAL_TZ,
        )

        self.assertEqual(history.days[0].distance_km, 2)

    def test_parses_bounded_days_and_builds_sensor_summary(self) -> None:
        start_date = date(2099, 1, 1)
        end_date = date(2099, 1, 7)
        fetched_at = datetime(2099, 1, 7, 4, 0, tzinfo=timezone.utc)
        response = json.loads((ROOT / "tests" / "fixtures" / "day_trip_range.json").read_text())

        history = trip_history.TripHistory.from_api(
            response,
            start_date=start_date,
            end_date=end_date,
            fetched_at=fetched_at,
            local_tz=LOCAL_TZ,
        )

        self.assertEqual([day.day for day in history.days], [end_date, date(2099, 1, 6)])
        self.assertEqual(history.days[1].effective_distance_km, 3.2)
        self.assertEqual(history.latest_trip.distance_km, 8.5)
        self.assertFalse(hasattr(history.latest_trip, "trip_id"))
        self.assertEqual(
            history.sensor_data(end_date),
            {
                "lastTripOdo": 8.5,
                "lastTripStartTime": datetime(2099, 1, 7, 10, 4, tzinfo=timezone.utc),
                "lastTripTime": 24,
                "todayOdo": 12.5,
                "recentSevenDayOdo": 15.7,
                "updatedAt": fetched_at,
                "archiveCoverageDays": 7,
                "archiveBackfillProgress": 1.9,
            },
        )

    def test_empty_success_is_zero_without_a_fake_latest_trip(self) -> None:
        history = trip_history.TripHistory.from_api(
            {"data": {"trips": []}},
            start_date=date(2099, 1, 1),
            end_date=date(2099, 1, 7),
            fetched_at=datetime(2099, 1, 7),
            local_tz=LOCAL_TZ,
        )

        summary = history.sensor_data(date(2099, 1, 7))
        self.assertIsNone(summary["lastTripOdo"])
        self.assertIsNone(summary["lastTripStartTime"])
        self.assertIsNone(summary["lastTripTime"])
        self.assertEqual(summary["todayOdo"], 0)
        self.assertEqual(summary["recentSevenDayOdo"], 0)
        self.assertEqual(summary["updatedAt"].tzinfo, timezone.utc)

    def test_rejects_missing_trip_list_and_invalid_values(self) -> None:
        with self.assertRaises(ValueError):
            trip_history.TripHistory.from_api(
                {"unexpected": []},
                start_date=date(2099, 1, 1),
                end_date=date(2099, 1, 7),
                fetched_at=datetime.now(timezone.utc),
                local_tz=LOCAL_TZ,
            )

        history = trip_history.TripHistory.from_api(
            {
                "trips": [
                    {
                        "date": "2099-01-07",
                        "odo": -1,
                        "trips": [
                            {
                                "tripOdo": -1,
                                "tripStartTime": "invalid",
                                "tripTime": -1,
                            }
                        ],
                    }
                ]
            },
            start_date=date(2099, 1, 1),
            end_date=date(2099, 1, 7),
            fetched_at=datetime.now(timezone.utc),
            local_tz=LOCAL_TZ,
        )
        self.assertEqual(history.days[0].trips, ())
        self.assertEqual(history.sensor_data(date(2099, 1, 7))["todayOdo"], 0)

    def test_storage_round_trip_is_sanitized(self) -> None:
        history = trip_history.TripHistory.from_api(
            {
                "trips": [
                    {
                        "date": "2099-01-07",
                        "odo": 7.5,
                        "unknownDayField": "discard-me",
                        "trips": [
                            {
                                "tripId": "synthetic-secret-id",
                                "tripOdo": 7.5,
                                "tripStartTime": "2099-01-07T10:00:00+08:00",
                                "tripTime": 20,
                                "avgSpeed": 30,
                                "maxSpeed": 60,
                                "avgPowerConsum": 18.5,
                                "avgFuelConsum": 0,
                                "startLatitude": 1.2,
                                "endLongitude": 3.4,
                                "unknownTripField": "discard-me",
                            }
                        ],
                    }
                ]
            },
            start_date=date(2099, 1, 1),
            end_date=date(2099, 1, 7),
            fetched_at=datetime(2099, 1, 7, 3, tzinfo=timezone.utc),
            local_tz=LOCAL_TZ,
        )

        stored = history.to_storage()
        stored_json = json.dumps(stored)

        self.assertEqual(trip_history.TripHistory.from_storage(stored), history)
        for forbidden in (
            "tripId",
            "Latitude",
            "Longitude",
            "unknownDayField",
            "unknownTripField",
            "synthetic-secret-id",
            "discard-me",
        ):
            self.assertNotIn(forbidden, stored_json)

    def test_merge_replaces_same_day_and_retains_empty_coverage(self) -> None:
        first = self._history(
            date(2099, 1, 1),
            date(2099, 1, 7),
            days=[{"date": "2099-01-07", "odo": 1, "trips": []}],
            fetched_hour=1,
        )
        replacement = self._history(
            date(2099, 1, 7),
            date(2099, 1, 7),
            days=[{"date": "2099-01-07", "odo": 2, "trips": []}],
            fetched_hour=2,
        )
        empty_older_window = self._history(
            date(2098, 12, 2),
            date(2098, 12, 31),
            days=[],
            fetched_hour=3,
        )

        merged = trip_history.TripHistory.merge(
            first,
            replacement,
            empty_older_window,
            retention_start=date(2098, 1, 8),
            retention_end=date(2099, 1, 7),
        )

        self.assertEqual(merged.days[0].distance_km, 2)
        self.assertEqual(
            merged.coverage_ranges,
            ((date(2098, 12, 2), date(2099, 1, 7)),),
        )
        self.assertEqual(
            trip_history.next_backfill_range(merged, today=date(2099, 1, 7)),
            (date(2098, 11, 2), date(2098, 12, 1)),
        )

    def test_recent_seven_day_summary_excludes_older_archive(self) -> None:
        today = date(2099, 1, 7)
        current = self._history(
            date(2099, 1, 1),
            today,
            days=[{"date": "2099-01-07", "odo": 8, "trips": []}],
        )
        older = self._history(
            date(2098, 12, 2),
            date(2098, 12, 31),
            days=[{"date": "2098-12-31", "odo": 99, "trips": []}],
        )
        merged = trip_history.TripHistory.merge(
            older,
            current,
            retention_start=date(2098, 1, 8),
            retention_end=today,
        )

        self.assertEqual(merged.sensor_data(today)["recentSevenDayOdo"], 8)

    def test_backfill_fills_recent_window_then_middle_gap(self) -> None:
        today = date(2099, 1, 7)
        self.assertEqual(
            trip_history.next_backfill_range(None, today=today),
            (date(2098, 12, 9), today),
        )

        current = self._history(date(2099, 1, 1), today)
        self.assertEqual(
            trip_history.next_backfill_range(current, today=today),
            (date(2098, 12, 2), date(2098, 12, 31)),
        )

        history_with_gap = trip_history.TripHistory(
            start_date=date(2098, 1, 8),
            end_date=today,
            fetched_at=datetime(2099, 1, 7, tzinfo=timezone.utc),
            days=(),
            coverage_ranges=(
                (date(2098, 1, 8), date(2098, 11, 8)),
                (date(2098, 12, 9), today),
            ),
        )
        self.assertEqual(
            trip_history.next_backfill_range(history_with_gap, today=today),
            (date(2098, 11, 9), date(2098, 12, 8)),
        )

    def test_full_year_completes_without_rounding_early(self) -> None:
        today = date(2099, 1, 7)
        full = self._history(date(2098, 1, 8), today)
        self.assertIsNone(trip_history.next_backfill_range(full, today=today))
        self.assertEqual(full.sensor_data(today)["archiveBackfillProgress"], 100)

        almost_full = self._history(date(2098, 1, 9), today)
        self.assertLess(
            almost_full.sensor_data(today)["archiveBackfillProgress"],
            100,
        )

    @staticmethod
    def _history(
        start_date: date,
        end_date: date,
        *,
        days: list[dict] | None = None,
        fetched_hour: int = 0,
    ):
        return trip_history.TripHistory.from_api(
            {"trips": days or []},
            start_date=start_date,
            end_date=end_date,
            fetched_at=datetime(2099, 1, 7, fetched_hour, tzinfo=timezone.utc),
            local_tz=LOCAL_TZ,
        )


if __name__ == "__main__":
    unittest.main()
