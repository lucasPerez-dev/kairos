"""Horaires par parc : récurrence hebdo, union des parcs, nocturnes."""

from __future__ import annotations

from datetime import date, datetime, time
from zoneinfo import ZoneInfo

from src.park_hours import ParkHours

MADRID = ZoneInfo("Europe/Madrid")

CONFIG = {
    "timezone": "Europe/Madrid",
    "parks": {
        "portaventura": {
            "weekly": {
                "mon": {"open": "10:30", "close": "18:00"},
                "sat": {"open": "10:30", "close": "23:00"},
                "sun": {"open": "10:30", "close": "19:00"},
            },
            "days": {"2026-10-31": {"open": "10:30", "close": "00:00"}},
            "closed": ["2026-12-25"],
            "default": {"open": "10:30", "close": "18:00"},
        },
        "ferrari_land": {"default": {"open": "16:30", "close": "22:00"}},
    },
}


def _hours() -> ParkHours:
    return ParkHours(CONFIG, "Europe/Madrid")


def _at(day: str, hm: str) -> datetime:
    return datetime.fromisoformat(f"{day}T{hm}").replace(tzinfo=MADRID)


def test_parks_listed() -> None:
    assert set(_hours().parks()) == {"portaventura", "ferrari_land"}


def test_weekly_recurrence() -> None:
    h = _hours()
    # vendredi → default semaine
    assert h.for_date(date(2026, 10, 16), "portaventura").close == time(18, 0)
    # samedi / dimanche
    assert h.for_date(date(2026, 10, 17), "portaventura").close == time(23, 0)
    assert h.for_date(date(2026, 10, 18), "portaventura").close == time(19, 0)
    # Ferrari Land identique tous les jours
    assert h.for_date(date(2026, 10, 16), "ferrari_land").open == time(16, 30)
    assert h.for_date(date(2026, 10, 17), "ferrari_land").close == time(22, 0)


def test_closed_date_has_no_hours() -> None:
    h = _hours()
    assert h.for_date(date(2026, 12, 25), "portaventura") is None
    assert h.phase(_at("2026-12-25", "12:00"), "portaventura") == "unknown"


def test_union_window_covers_both_parks() -> None:
    h = _hours()
    open_dt, close_dt = h.window(date(2026, 10, 16))
    assert open_dt.strftime("%H:%M") == "10:30"   # PortAventura ouvre
    assert close_dt.strftime("%H:%M") == "22:00"  # Ferrari Land ferme en dernier


def test_phase_is_per_park() -> None:
    h = _hours()
    evening = _at("2026-10-16", "21:00")  # PortAventura fermé, Ferrari Land ouvert
    assert h.phase(evening, "portaventura") == "closed"
    assert h.phase(evening, "ferrari_land") == "closing"
    assert h.phase(evening) == "closing"  # union : le complexe est ouvert
    assert h.minutes_to_close(evening, "ferrari_land") == 60.0


def test_overnight_close() -> None:
    h = _hours()
    # 31/10 : PortAventura ouvert jusqu'à minuit
    assert h.phase(_at("2026-10-31", "23:30"), "portaventura") == "closing"
    assert h.minutes_to_close(_at("2026-10-31", "23:30"), "portaventura") == 30.0


def test_staleness_warning() -> None:
    now = _at("2026-10-20", "12:00")
    unverified = ParkHours({**CONFIG, "_verified": False, "_updated": "2026-10-16"}, "Europe/Madrid")
    assert unverified.staleness_warning(now)

    verified_recent = ParkHours(
        {**CONFIG, "_verified": True, "_updated": "2026-10-16"}, "Europe/Madrid"
    )
    assert verified_recent.staleness_warning(now) is None

    verified_old = ParkHours(
        {**CONFIG, "_verified": True, "_updated": "2026-01-01"}, "Europe/Madrid"
    )
    assert "jours" in (verified_old.staleness_warning(now) or "")


def test_legacy_flat_config_still_supported() -> None:
    h = ParkHours(
        {"timezone": "Europe/Madrid", "default": {"open": "10:00", "close": "20:00"}},
        "Europe/Madrid",
    )
    assert h.phase(_at("2026-10-16", "12:00")) == "peak"
    assert h.phase(_at("2026-10-16", "21:00")) == "closed"
