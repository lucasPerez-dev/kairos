from __future__ import annotations

from datetime import datetime, timedelta, timezone
from zoneinfo import ZoneInfo

import pytest

from src.engine import Engine, Zones, _slope_per_minute
from src.models import Ride
from src.park_hours import ParkHours
from src.store import Store

MADRID = ZoneInfo("Europe/Madrid")


def _hours() -> ParkHours:
    config = {
        "timezone": "Europe/Madrid",
        "default": {"open": "10:00", "close": "20:00"},
        "days": {"2027-04-02": {"open": "10:00", "close": "20:00"}},
    }
    return ParkHours(config, "Europe/Madrid")


def _zones() -> Zones:
    return Zones(
        {
            "walk_speed_m_per_min": 70,
            "layout_scale_meters": 10,
            "zones": {"A": [0, 0], "B": [0, 7], "C": [10, 0]},
        }
    )


# ------------------------------------------------------------------ slope --- #


def test_slope_per_minute_negative_when_dropping() -> None:
    t0 = datetime(2027, 4, 2, 10, 0, tzinfo=timezone.utc)
    points = [
        (t0, 40, True),
        (t0 + timedelta(minutes=15), 30, True),
        (t0 + timedelta(minutes=30), 20, True),
    ]
    assert _slope_per_minute(points) == pytest.approx(-20 / 30)


def test_slope_flat_without_variance() -> None:
    t0 = datetime(2027, 4, 2, 10, 0, tzinfo=timezone.utc)
    points = [(t0, 25, True), (t0 + timedelta(minutes=5), 25, True)]
    assert _slope_per_minute(points) == 0.0


# ------------------------------------------------------------------ zones --- #


def test_zones_travel_minutes() -> None:
    zones = _zones()
    assert zones.travel_minutes("A", "B") == pytest.approx(1.0)
    assert zones.travel_minutes("A", "A") == 0.0
    assert zones.travel_minutes(None, "B") == 0.0
    assert zones.travel_minutes("A", "inconnue") == pytest.approx(4.0)


# ------------------------------------------------------------- park hours --- #


@pytest.mark.parametrize(
    ("hour", "minute", "expected"),
    [
        (8, 0, "before_open"),
        (10, 30, "opening"),
        (12, 0, "peak"),
        (16, 0, "afternoon"),
        (19, 0, "closing"),
        (21, 0, "closed"),
    ],
)
def test_phase(hour: int, minute: int, expected: str) -> None:
    now = datetime(2027, 4, 2, hour, minute, tzinfo=MADRID)
    assert _hours().phase(now) == expected


# ----------------------------------------------------------------- engine --- #


def test_engine_detects_falling_trend_and_recommends(tmp_path) -> None:
    store = Store(tmp_path / "t.db")
    store.sync_rides([Ride(1, "Test Coaster", "portaventura", "Chine", "coaster", 3)])
    now = datetime(2027, 4, 2, 12, 0, tzinfo=timezone.utc)
    store.add_snapshots(
        [
            (1, now - timedelta(minutes=30), 50, True),
            (1, now - timedelta(minutes=15), 35, True),
            (1, now - timedelta(minutes=5), 20, True),
        ]
    )
    engine = Engine(store, _hours(), _zones())

    delta, label = engine.trend(1, now)
    assert label == "falling"
    assert delta < 0

    recos = engine.recommend(current_zone=None, limit=3, now=now)
    assert len(recos) == 1
    assert recos[0].ride.id == 1
    store.close()


def test_recommend_can_filter_category(tmp_path) -> None:
    store = Store(tmp_path / "c.db")
    store.sync_rides(
        [
            Ride(1, "Coaster", "portaventura", "Chine", "coaster", 3),
            Ride(2, "Family", "portaventura", "Chine", "family", 3),
        ]
    )
    now = datetime(2027, 4, 2, 12, 0, tzinfo=timezone.utc)
    store.add_snapshots([(1, now, 10, True), (2, now, 10, True)])
    engine = Engine(store, _hours(), _zones())

    recos = engine.recommend(current_zone=None, limit=5, now=now, categories={"coaster"})
    assert [r.ride.id for r in recos] == [1]
    store.close()


def test_engine_excludes_done_rides(tmp_path) -> None:
    store = Store(tmp_path / "t.db")
    store.sync_rides([Ride(1, "Test Coaster", "portaventura", "Chine", "coaster", 3)])
    now = datetime(2027, 4, 2, 12, 0, tzinfo=timezone.utc)
    store.add_snapshots([(1, now, 20, True)])
    store.mark_done(1)

    engine = Engine(store, _hours(), _zones())
    assert engine.recommend(current_zone=None, limit=3, now=now) == []
    store.close()
