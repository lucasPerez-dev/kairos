"""Point d'entrée de Kairos : bot Telegram + dashboard + polling planifié."""

from __future__ import annotations

import asyncio
import logging
import signal
import sys

import uvicorn
from apscheduler.schedulers.asyncio import AsyncIOScheduler

from src.alerts import Alerter
from src.config import Settings, get_settings, load_json
from src.engine import Engine, Zones
from src.fetcher import QueueTimesFetcher
from src.models import Ride
from src.park_hours import ParkHours
from src.service import KairosService
from src.store import Store

logger = logging.getLogger("kairos")


def setup_logging() -> None:
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s %(levelname)-7s %(name)s | %(message)s",
        datefmt="%H:%M:%S",
    )
    logging.getLogger("httpx").setLevel(logging.WARNING)
    logging.getLogger("telegram").setLevel(logging.INFO)
    logging.getLogger("apscheduler").setLevel(logging.WARNING)


def build_service(settings: Settings) -> KairosService:
    rides_config = load_json(settings.resolve(settings.rides_config))
    hours_config = load_json(settings.resolve(settings.hours_config))
    zones_config = load_json(settings.resolve(settings.zones_config))

    store = Store(settings.resolve(settings.db_path))

    rides = [
        Ride(
            id=int(item["id"]),
            name=item["name"],
            park=item["park"],
            zone=item.get("zone", ""),
            category=item.get("category", "family"),
            priority=int(item.get("priority", 2)),
        )
        for item in rides_config["rides"]
    ]
    store.sync_rides(rides)

    parks = rides_config["parks"]
    park_ids = {park["key"]: int(park["queue_times_id"]) for park in parks}

    fetcher = QueueTimesFetcher(park_ids, force_ipv4=settings.force_ipv4)
    hours = ParkHours(hours_config, settings.timezone)
    zones = Zones(zones_config)
    engine = Engine(store, hours, zones)
    alerter = Alerter(store, settings)

    return KairosService(settings, store, fetcher, engine, hours, alerter, parks)


async def _shutdown_quietly(app) -> None:
    """Arrête un Application PTB sans jamais lever d'exception (déjà partiellement arrêté)."""
    try:
        if app.updater and app.updater.running:
            await app.updater.stop()
    except Exception:  # noqa: BLE001
        pass
    try:
        if app.running:
            await app.stop()
    except Exception:  # noqa: BLE001
        pass
    try:
        await app.shutdown()
    except Exception:  # noqa: BLE001
        pass


async def run() -> None:
    settings = get_settings()
    service = build_service(settings)
    logger.info("Kairos démarré · mode=%s · %d attractions", service.mode, len(service.rides()))

    scheduler = AsyncIOScheduler(timezone=settings.timezone)
    # misfire_grace_time=None : APScheduler ne considère jamais un job comme « manqué ».
    # Indispensable sur une machine qui se met en veille : au réveil, l'horloge a sauté de
    # plusieurs minutes/heures et les jobs seraient sinon tous signalés comme manqués.
    # Comme coalesce=True et max_instances=1, un job en retard ne s'exécute qu'une seule fois,
    # et nos handlers sont idempotents : on obtient exactement un rafraîchissement au réveil.
    scheduler.add_job(
        service.poll_once, "interval", seconds=settings.poll_interval_seconds,
        id="poll", max_instances=1, coalesce=True, misfire_grace_time=None,
    )
    scheduler.add_job(
        service.check_timeline, "interval", minutes=1,
        id="timeline", max_instances=1, coalesce=True, misfire_grace_time=None,
    )

    bot_app = None
    if settings.bot_enabled:
        from src.bot import build_application, log_polling_error, make_notifier

        bot_app = build_application(service, settings)
        service.notifier = make_notifier(bot_app, service, settings)
        for attempt in range(1, 4):
            try:
                await bot_app.initialize()
                await bot_app.start()
                await bot_app.updater.start_polling(error_callback=log_polling_error)
                logger.info("Bot Telegram démarré (long polling)")
                break
            except Exception as exc:  # noqa: BLE001 - panne réseau transitoire
                logger.warning("Démarrage du bot échoué (tentative %d/3) : %s", attempt, exc)
                await _shutdown_quietly(bot_app)
                await asyncio.sleep(5)
        else:
            logger.error(
                "Bot Telegram indisponible — le dashboard et le polling continuent sans alertes."
            )
            bot_app = None
            service.notifier = None
    else:
        logger.warning("Aucun token Telegram : bot désactivé, dashboard seul.")

    server = None
    server_task: asyncio.Task | None = None
    if settings.dashboard_enabled:
        from src.dashboard import build_dashboard

        dash_app = build_dashboard(service, settings)
        config = uvicorn.Config(
            dash_app, host=settings.dashboard_host, port=settings.dashboard_port, log_level="warning"
        )
        server = uvicorn.Server(config)
        server_task = asyncio.create_task(server.serve())
        logger.info("Dashboard : http://%s:%d", settings.dashboard_host, settings.dashboard_port)

    scheduler.start()
    await service.poll_once()

    stop = asyncio.Event()
    loop = asyncio.get_running_loop()
    for sig in (signal.SIGINT, signal.SIGTERM):
        try:
            loop.add_signal_handler(sig, stop.set)
        except NotImplementedError:  # pragma: no cover - plateformes sans signaux
            pass
    await stop.wait()

    logger.info("Arrêt en cours…")
    scheduler.shutdown(wait=False)
    if server is not None:
        server.should_exit = True
    if server_task is not None:
        try:
            await asyncio.wait_for(server_task, timeout=5)
        except (asyncio.TimeoutError, asyncio.CancelledError):
            server_task.cancel()
    if bot_app is not None:
        await _shutdown_quietly(bot_app)
    await service.fetcher.aclose()
    service.store.close()
    logger.info("Au revoir.")


def main() -> int:
    setup_logging()
    try:
        asyncio.run(run())
    except KeyboardInterrupt:  # pragma: no cover
        pass
    return 0


if __name__ == "__main__":
    sys.exit(main())
