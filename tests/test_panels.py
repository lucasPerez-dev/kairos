from __future__ import annotations

from src.alerts import Alerter
from src.bot import LIST_PAGE_SIZE, panel_liste, panel_menu, panel_reglages, panel_zone
from src.config import Settings
from src.engine import Engine, Zones
from src.fetcher import QueueTimesFetcher
from src.models import Ride
from src.park_hours import ParkHours
from src.service import KairosService
from src.store import Store


def make_service(tmp_path, n_rides: int = 14) -> KairosService:
    store = Store(tmp_path / "p.db")
    store.sync_rides(
        [
            Ride(id=i, name=f"Ride {i:02d}", park="portaventura", zone="Chine", category="coaster", priority=2)
            for i in range(1, n_rides + 1)
        ]
    )
    settings = Settings(db_path=str(tmp_path / "p.db"))
    hours = ParkHours(
        {"timezone": "Europe/Madrid", "default": {"open": "10:00", "close": "20:00"}},
        "Europe/Madrid",
    )
    engine = Engine(store, hours, Zones({"zones": {"Chine": [50, 50]}}))
    alerter = Alerter(store, settings)
    fetcher = QueueTimesFetcher({"portaventura": 19})
    parks = [{"key": "portaventura", "name": "PortAventura Park", "queue_times_id": 19}]
    return KairosService(settings, store, fetcher, engine, hours, alerter, parks)


def test_menu_panel_shows_state(tmp_path) -> None:
    service = make_service(tmp_path)
    text, markup = panel_menu(service)
    assert "Kairos" in text
    assert "Zone" in text
    assert len(markup.inline_keyboard) >= 2


def test_liste_panel_pagination(tmp_path) -> None:
    service = make_service(tmp_path, n_rides=14)
    text, markup = panel_liste(service, page=0)
    # une ligne par attraction + navigation + retour
    assert len(markup.inline_keyboard) == LIST_PAGE_SIZE + 2
    assert "0" not in text.split("—")[0]  # garde-fou simple
    _, last = panel_liste(service, page=99)
    assert last.inline_keyboard[-2][1].text == "3/3"


def test_toggle_reflected_in_liste(tmp_path) -> None:
    service = make_service(tmp_path, n_rides=3)
    service.store.mark_done(1)
    text, markup = panel_liste(service, page=0)
    labels = [row[0].text for row in markup.inline_keyboard[:3]]
    assert labels[0].startswith("↩️")  # faite → proposée en décochage
    assert "1/3" in text


def test_reglages_panel_threshold_and_mode(tmp_path) -> None:
    service = make_service(tmp_path)
    text, markup = panel_reglages(service)
    assert "Seuil d'alerte" in text
    assert "collecte" in text
    datas = [btn.callback_data for row in markup.inline_keyboard for btn in row]
    assert "seuil:-1" in datas and "mode:toggle" in datas


def test_zone_panel_lists_zones(tmp_path) -> None:
    service = make_service(tmp_path)
    _, markup = panel_zone(service)
    datas = [btn.callback_data for row in markup.inline_keyboard for btn in row]
    assert "zone:set:0" in datas


def test_menu_has_refresh_button(tmp_path) -> None:
    service = make_service(tmp_path)
    _, markup = panel_menu(service)
    datas = [btn.callback_data for row in markup.inline_keyboard for btn in row]
    assert "menu:refresh" in datas


def test_reglages_has_test_and_refresh_buttons(tmp_path) -> None:
    service = make_service(tmp_path)
    _, markup = panel_reglages(service)
    datas = [btn.callback_data for row in markup.inline_keyboard for btn in row]
    assert "test:notify" in datas and "menu:refresh" in datas
