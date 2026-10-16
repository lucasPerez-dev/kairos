"""Fiche attraction : image, description, crédit et profil horaire."""

from __future__ import annotations

from datetime import datetime, timedelta, timezone

from src.alerts import Alerter
from src.config import Settings
from src.engine import Engine, Zones
from src.models import Ride
from src.park_hours import ParkHours
from src.service import KairosService
from src.store import Store


class FakeFetcher:
    async def fetch_all(self):
        return {}

    async def aclose(self) -> None:  # pragma: no cover
        pass


def _service(tmp_path, images_dir):
    desc = tmp_path / "desc.json"
    desc.write_text('{"1": "Un classique B&M."}', encoding="utf-8")
    cred = tmp_path / "cred.json"
    cred.write_text('{"1": {"author": "Jane Doe", "license": "CC0", "source": "x"}}', encoding="utf-8")

    store = Store(tmp_path / "r.db")
    store.sync_rides([Ride(1, "Dragon Khan", "portaventura", "Chine", "coaster", 3)])
    settings = Settings(
        db_path=str(tmp_path / "r.db"),
        telegram_chat_id="",
        rides_images_dir=str(images_dir),
        descriptions_config=str(desc),
        credits_config=str(cred),
    )
    hours = ParkHours({"timezone": "Europe/Madrid", "default": {"open": "10:00", "close": "22:00"}}, "Europe/Madrid")
    service = KairosService(
        settings, store, FakeFetcher(), Engine(store, hours, Zones({"zones": {"Chine": [0, 0]}})),
        hours, Alerter(store, settings),
        [{"key": "portaventura", "name": "PortAventura Park", "queue_times_id": 19}],
    )
    return service, store


def test_image_url_none_then_found(tmp_path) -> None:
    images = tmp_path / "images"
    images.mkdir()
    service, store = _service(tmp_path, images)

    assert service.ride_image_url(1) is None
    (images / "1.jpg").write_bytes(b"fake")
    assert service.ride_image_url(1) == "/static/images/1.jpg"
    store.close()


def test_ride_detail_has_profile_and_metadata(tmp_path) -> None:
    images = tmp_path / "images"
    images.mkdir()
    (images / "1.png").write_bytes(b"fake")
    service, store = _service(tmp_path, images)

    now = datetime.now(timezone.utc)
    store.add_snapshots(
        [
            (1, now - timedelta(hours=3), 99, False),  # fermée : hors profil
            (1, now - timedelta(hours=2), 40, True),
            (1, now, 12, True),  # dernière observation (ouverte)
        ]
    )

    detail = service.ride_detail(1)
    assert detail is not None
    assert detail["name"] == "Dragon Khan"
    assert detail["description"] == "Un classique B&M."
    assert detail["credit"]["author"] == "Jane Doe"
    assert detail["image"] == "/static/images/1.png"
    assert detail["wait_minutes"] == 12
    assert detail["hourly"]  # profil non vide (uniquement les observations ouvertes)
    assert all(p["avg"] <= 40 for p in detail["hourly"])
    assert detail["best_hour"] is not None
    assert service.ride_detail(999) is None
    store.close()
