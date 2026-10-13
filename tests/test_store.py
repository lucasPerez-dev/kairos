from __future__ import annotations

from datetime import datetime, timedelta, timezone

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
