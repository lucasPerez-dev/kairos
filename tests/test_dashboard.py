"""Endpoints du dashboard : page, état, actualisation forcée."""

from __future__ import annotations

from fastapi.testclient import TestClient

from src.alerts import Alerter
from src.config import Settings
from src.dashboard import build_dashboard
from src.engine import Engine, Zones
from src.models import Ride
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


def _client(tmp_path) -> tuple[TestClient, KairosService, FakeFetcher]:
    store = Store(tmp_path / "d.db")
    store.sync_rides([Ride(1, "Dragon Khan", "portaventura", "Chine", "coaster", 3)])
    settings = Settings(db_path=str(tmp_path / "d.db"), telegram_chat_id="")
    hours = ParkHours(
        {"timezone": "Europe/Madrid", "default": {"open": "10:00", "close": "20:00"}},
        "Europe/Madrid",
    )
    fetcher = FakeFetcher()
    service = KairosService(
        settings, store, fetcher, Engine(store, hours, Zones({"zones": {"Chine": [0, 0]}})),
        hours, Alerter(store, settings),
        [{"key": "portaventura", "name": "PortAventura Park", "queue_times_id": 19}],
    )
    return TestClient(build_dashboard(service, settings)), service, fetcher


def test_page_and_state(tmp_path) -> None:
    client, _service, _fetcher = _client(tmp_path)
    assert client.get("/").status_code == 200

    body = client.get("/api/state").json()
    for key in (
        "phase", "parks", "rides", "recommendations", "coaster_recommendations",
        "alert_threshold", "hours_warning",
    ):
        assert key in body
    assert body["parks"][0]["key"] == "portaventura"
    assert "phase" in body["parks"][0]


def test_ride_endpoint(tmp_path) -> None:
    client, _service, _fetcher = _client(tmp_path)
    body = client.get("/api/ride/1").json()
    for key in ("name", "description", "image", "credit", "hourly", "best_hour", "wait_minutes"):
        assert key in body
    assert body["name"] == "Dragon Khan"
    assert client.get("/api/ride/9999").status_code == 404


def test_refresh_triggers_poll(tmp_path) -> None:
    client, service, fetcher = _client(tmp_path)
    # On force le poll même si le parc est fermé, pour vérifier que l'endpoint déclenche bien.
    service.settings.skip_when_closed = False
    resp = client.post("/api/refresh")
    assert resp.status_code == 200
    assert resp.json()["ok"] is True
    assert fetcher.calls == 1
    assert client.get("/healthz").json()["ok"] is True
