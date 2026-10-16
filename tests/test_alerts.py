"""Alertes : type réel journalisé et regroupement des vagues par parc."""

from __future__ import annotations

from src.alerts import Alerter
from src.config import Settings
from src.engine import Engine, Zones
from src.fetcher import QueueTimesFetcher
from src.models import Alert, Ride, RideStatus
from src.park_hours import ParkHours
from src.service import KairosService
from src.store import Store

RIDE = Ride(id=1, name="Dragon Khan", park="portaventura", zone="Chine", category="coaster", priority=3)


def _alerter(tmp_path) -> tuple[Store, Alerter]:
    store = Store(tmp_path / "a.db")
    settings = Settings(
        db_path=str(tmp_path / "a.db"),
        alert_default_threshold_minutes=25,
        alert_cooldown_minutes=30,
    )
    return store, Alerter(store, settings)


def _service(tmp_path) -> KairosService:
    store = Store(tmp_path / "s.db")
    settings = Settings(db_path=str(tmp_path / "s.db"), telegram_chat_id="")
    hours = ParkHours({"default": {"open": "10:00", "close": "20:00"}}, "Europe/Madrid")
    engine = Engine(store, hours, Zones({"zones": {}}))
    parks = [
        {"key": "portaventura", "name": "PortAventura Park", "queue_times_id": 19},
        {"key": "ferrari_land", "name": "Ferrari Land", "queue_times_id": 277},
    ]
    return KairosService(
        settings, store, QueueTimesFetcher({"portaventura": 19}), engine, hours, Alerter(store, settings), parks
    )


def _kinds(store: Store) -> list[str]:
    return [r["kind"] for r in store._conn.execute("SELECT kind FROM alerts ORDER BY id")]


def test_threshold_alert_is_logged_as_threshold(tmp_path) -> None:
    store, alerter = _alerter(tmp_path)
    alerter.prime([RideStatus(ride=RIDE, is_open=True, wait_minutes=30)])
    alerts = alerter.evaluate([RideStatus(ride=RIDE, is_open=True, wait_minutes=10)])
    assert [a.kind for a in alerts] == ["threshold"]
    assert _kinds(store) == ["threshold"]


def test_reopened_alert_is_logged_as_reopened(tmp_path) -> None:
    store, alerter = _alerter(tmp_path)
    alerter.prime([RideStatus(ride=RIDE, is_open=False)])
    alerts = alerter.evaluate([RideStatus(ride=RIDE, is_open=True, wait_minutes=10)])
    assert [a.kind for a in alerts] == ["reopened"]
    assert _kinds(store) == ["reopened"]  # avant le correctif, c'était "threshold"


def test_cooldown_applies_across_kinds(tmp_path) -> None:
    store, alerter = _alerter(tmp_path)
    alerter.prime([RideStatus(ride=RIDE, is_open=True, wait_minutes=30)])
    assert len(alerter.evaluate([RideStatus(ride=RIDE, is_open=True, wait_minutes=10)])) == 1
    # Réouverture immédiate : doit être bloquée par le cooldown (toutes catégories).
    assert alerter.evaluate([RideStatus(ride=RIDE, is_open=True, wait_minutes=5)]) == []


def test_single_alert_keeps_classic_format(tmp_path) -> None:
    service = _service(tmp_path)
    alert = Alert(ride=RIDE, wait_minutes=10, threshold=25, kind="threshold")
    messages = service.format_alerts([alert])
    assert messages == [service.format_alert(alert)]


def test_format_alerts_groups_by_park(tmp_path) -> None:
    service = _service(tmp_path)
    rides = [
        Ride(1, "Dragon Khan", "portaventura", "Chine", "coaster", 3),
        Ride(2, "Shambhala", "portaventura", "Chine", "coaster", 3),
        Ride(3, "Red Force", "ferrari_land", "Ferrari Land", "coaster", 3),
    ]
    alerts = [
        Alert(ride=rides[0], wait_minutes=10, threshold=25, kind="threshold"),
        Alert(ride=rides[1], wait_minutes=0, threshold=25, kind="reopened"),
        Alert(ride=rides[2], wait_minutes=20, threshold=25, kind="threshold"),
    ]
    messages = service.format_alerts(alerts)
    assert len(messages) == 2  # un message par parc
    portaventura_msg = next(m for m in messages if "PortAventura Park" in m)
    ferrari_msg = next(m for m in messages if "Ferrari Land" in m)
    assert "2 attractions se libèrent" in portaventura_msg
    assert "Dragon Khan" in portaventura_msg and "Shambhala" in portaventura_msg
    assert "1 attraction se libère" in ferrari_msg
    assert "Red Force" in ferrari_msg
