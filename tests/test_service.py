from __future__ import annotations

from src.alerts import Alerter
from src.config import Settings
from src.engine import Engine, Zones
from src.models import RawRide, Ride
from src.park_hours import ParkHours
from src.service import KairosService
from src.store import Store


class FakeFetcher:
    def __init__(self) -> None:
        self.calls = 0

    async def fetch_all(self):
        self.calls += 1
        return {}

    async def aclose(self) -> None:  # pragma: no cover
        pass


class StateFetcher:
    def __init__(self, states: list[list[RawRide]]) -> None:
        self.states = list(states)
        self.calls = 0

    async def fetch_all(self):
        self.calls += 1
        return {"portaventura": self.states.pop(0)}

    async def aclose(self) -> None:  # pragma: no cover
        pass


def make_service(tmp_path) -> tuple[KairosService, FakeFetcher]:
    store = Store(tmp_path / "s.db")
    settings = Settings(db_path=str(tmp_path / "s.db"), telegram_bot_token="", mode="collect")
    hours = ParkHours(
        {"timezone": "Europe/Madrid", "default": {"open": "10:00", "close": "20:00"}},
        "Europe/Madrid",
    )
    engine = Engine(store, hours, Zones({"zones": {"Chine": [50, 50]}}))
    alerter = Alerter(store, settings)
    fetcher = FakeFetcher()
    service = KairosService(
        settings, store, fetcher, engine, hours, alerter,
        [{"key": "portaventura", "name": "PortAventura Park", "queue_times_id": 19}],
    )
    return service, fetcher


async def test_poll_skipped_when_closed(tmp_path) -> None:
    service, fetcher = make_service(tmp_path)
    service.hours.phase = lambda *a, **k: "closed"  # type: ignore[method-assign]
    await service.poll_once()
    assert fetcher.calls == 0


async def test_poll_runs_when_open(tmp_path) -> None:
    service, fetcher = make_service(tmp_path)
    service.hours.phase = lambda *a, **k: "afternoon"  # type: ignore[method-assign]
    await service.poll_once()
    assert fetcher.calls == 1


async def test_poll_forced_even_when_closed_if_disabled(tmp_path) -> None:
    service, fetcher = make_service(tmp_path)
    service.settings.skip_when_closed = False
    service.hours.phase = lambda *a, **k: "closed"  # type: ignore[method-assign]
    await service.poll_once()
    assert fetcher.calls == 1


async def test_only_closed_transitions_are_stored(tmp_path) -> None:
    store = Store(tmp_path / "t.db")
    store.sync_rides([Ride(1, "Coaster", "portaventura", "Chine", "coaster", 2)])
    settings = Settings(
        db_path=str(tmp_path / "t.db"),
        telegram_bot_token="",
        mode="collect",
        skip_when_closed=False,
    )
    hours = ParkHours({"default": {"open": "00:00", "close": "23:59"}}, "Europe/Madrid")
    fetcher = StateFetcher(
        [
            [RawRide(1, True, 10)],   # ouvert -> stocké
            [RawRide(1, False, 0)],   # transition ouvert->fermé -> stocké
            [RawRide(1, False, 0)],   # toujours fermé -> ignoré
        ]
    )
    service = KairosService(
        settings,
        store,
        fetcher,
        Engine(store, hours, Zones({"zones": {"Chine": [0, 0]}})),
        hours,
        Alerter(store, settings),
        [{"key": "portaventura", "name": "PortAventura Park", "queue_times_id": 19}],
    )

    await service.poll_once()
    await service.poll_once()
    await service.poll_once()

    assert store.stats()["snapshots"] == 2
    store.close()
