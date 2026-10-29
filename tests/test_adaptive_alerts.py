"""Alerting adaptatif : baseline par attraction/heure, sensibilité, override."""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
from zoneinfo import ZoneInfo

from src.alerts import Alerter
from src.config import Settings
from src.models import Ride, RideStatus
from src.store import Store

TZ = ZoneInfo("Europe/Madrid")
RIDE = Ride(1, "Shambhala", "portaventura", "Chine", "coaster", 3)


def _alerter(tmp_path) -> tuple[Store, Alerter]:
    store = Store(tmp_path / "a.db")
    settings = Settings(
        db_path=str(tmp_path / "a.db"),
        alert_default_threshold_minutes=25,
        alert_cooldown_minutes=30,
        timezone="Europe/Madrid",
    )
    return store, Alerter(store, settings)


def _add_baseline(store: Store, waits: list[int]) -> None:
    """Ajoute 2 observations par jour sur 5 jours, à la même heure locale que maintenant."""
    now = datetime.now(timezone.utc).astimezone(TZ)
    base = now.replace(minute=10, second=0, microsecond=0)
    for k, wait in enumerate(waits):
        day = (k // 2) + 1
        ts = base - timedelta(days=day) + timedelta(minutes=10 * (k % 2))
        store.add_snapshots([(RIDE.id, ts, wait, True)])


def _status(wait: int) -> RideStatus:
    return RideStatus(ride=RIDE, is_open=True, wait_minutes=wait)


def test_adaptive_alerts_when_unusually_low(tmp_path) -> None:
    store, alerter = _alerter(tmp_path)
    _add_baseline(store, [40] * 10)
    alerter.prime([_status(50)])

    alerts = alerter.evaluate([_status(5)])
    assert len(alerts) == 1
    assert alerts[0].baseline_median == 40


def test_adaptive_ignores_always_cheap_hours(tmp_path) -> None:
    store, alerter = _alerter(tmp_path)
    _add_baseline(store, [0] * 10)  # habituellement 0 min à cette heure
    alerter.prime([_status(0)])

    assert alerter.evaluate([_status(0)]) == []


def test_manual_override_wins(tmp_path) -> None:
    store, alerter = _alerter(tmp_path)
    _add_baseline(store, [40] * 10)
    alerter.set_ride_threshold(RIDE.id, 10)
    alerter.prime([_status(50)])

    alerts = alerter.evaluate([_status(5)])
    assert len(alerts) == 1
    assert alerts[0].threshold == 10
    assert alerts[0].baseline_median is None


def test_sensitivity_widens_threshold(tmp_path) -> None:
    store, alerter = _alerter(tmp_path)
    _add_baseline(store, [0, 5, 10, 15, 20, 25, 30, 35, 40, 45])
    now = datetime.now(timezone.utc)

    alerter.set_alert_sensitivity("prudent")
    cut_prudent, _ = alerter._cut(RIDE.id, now)
    alerter.set_alert_sensitivity("agressif")
    cut_agressif, _ = alerter._cut(RIDE.id, now)

    assert cut_prudent is not None and cut_agressif is not None
    assert cut_agressif >= cut_prudent
    assert alerter.alert_sensitivity() == "agressif"


def test_cold_start_falls_back_to_fixed_threshold(tmp_path) -> None:
    store, alerter = _alerter(tmp_path)  # aucune observation -> baseline vide
    alerter.prime([_status(50)])
    alerts = alerter.evaluate([_status(10)])
    assert len(alerts) == 1
    assert alerts[0].threshold == 25
    assert alerts[0].baseline_median is None
