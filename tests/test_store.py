from __future__ import annotations

import sqlite3
from datetime import datetime, timedelta, timezone

from src.models import Ride
from src.store import Store


def test_snapshots_latest_and_history(tmp_path) -> None:
    store = Store(tmp_path / "s.db")
    now = datetime.now(timezone.utc)
    store.add_snapshots(
        [
            (5, now - timedelta(minutes=10), 40, True),
            (5, now, 25, True),
        ]
    )
    latest = store.latest()
    assert latest[5]["wait"] == 25
    assert latest[5]["is_open"] is True
    assert len(store.history(5, minutes=60, now=now)) == 2
    store.close()


def test_done_and_state_roundtrip(tmp_path) -> None:
    store = Store(tmp_path / "s2.db")
    store.mark_done(7)
    assert store.done_ids() == {7}
    store.unmark_done(7)
    assert store.done_ids() == set()

    store.set_state("current_zone", "Chine")
    assert store.get_state("current_zone") == "Chine"
    assert store.get_state("absent", default="defaut") == "defaut"
    store.close()


def test_duplicate_snapshots_are_ignored(tmp_path) -> None:
    store = Store(tmp_path / "dup.db")
    now = datetime.now(timezone.utc)
    store.add_snapshots([(1, now, 30, True)])
    store.add_snapshots([(1, now, 30, True)])  # même (ride_id, ts) -> ignoré
    assert store.stats()["snapshots"] == 1
    store.close()


def test_migration_dedupes_and_enforces_uniqueness(tmp_path) -> None:
    path = tmp_path / "old.db"
    con = sqlite3.connect(path)
    con.executescript(
        """
        CREATE TABLE snapshots (
            id INTEGER PRIMARY KEY AUTOINCREMENT, ride_id INTEGER NOT NULL,
            ts TEXT NOT NULL, wait INTEGER NOT NULL, is_open INTEGER NOT NULL
        );
        CREATE INDEX idx_snapshots_ride_ts ON snapshots(ride_id, ts);
        """
    )
    con.execute("INSERT INTO snapshots (ride_id, ts, wait, is_open) VALUES (1, '2026-01-01T10:00:00+00:00', 10, 1)")
    con.execute("INSERT INTO snapshots (ride_id, ts, wait, is_open) VALUES (1, '2026-01-01T10:00:00+00:00', 10, 1)")
    con.commit()
    con.close()

    store = Store(path)  # la migration dédoublonne et crée l'index unique
    assert store.stats()["snapshots"] == 1
    store.add_snapshots([(1, datetime(2026, 1, 1, 10, tzinfo=timezone.utc), 10, True)])
    assert store.stats()["snapshots"] == 1
    store.close()


def test_hidden_rides_are_filtered(tmp_path) -> None:
    store = Store(tmp_path / "s3.db")
    store.sync_rides(
        [
            Ride(id=1, name="Visible", park="p", zone="z", category="family", priority=2),
            Ride(id=2, name="Fantome", park="p", zone="z", category="family", priority=2),
        ]
    )
    assert {r.id for r in store.rides()} == {1, 2}

    store.set_ride_hidden(2, True)
    assert {r.id for r in store.rides()} == {1}
    # get_state tolère une valeur absente/inattendue
    assert store.hidden_ride_ids() == {2}
    assert {r.id for r in store.rides(include_hidden=True)} == {1, 2}

    store.set_ride_hidden(2, False)
    assert {r.id for r in store.rides()} == {1, 2}
    assert store.hidden_ride_ids() == set()
    store.close()
