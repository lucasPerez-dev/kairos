"""Service central : orchestration du polling, des alertes et du briefing."""

from __future__ import annotations

import asyncio
import json
import logging
import math
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Awaitable, Callable

from .alerts import Alerter
from .config import Settings, load_json
from .engine import Engine, trend_arrow
from .fetcher import QueueTimesFetcher
from .models import Ride
from .park_hours import ParkHours
from .store import Store, iso, utcnow

IMAGE_EXTS = (".jpg", ".jpeg", ".png", ".webp")


def _load_map(path: str | Path) -> dict[str, Any]:
    """Charge un fichier JSON id→valeur, en ignorant les clés de commentaire. Silencieux si absent."""
    try:
        data = load_json(path)
    except (OSError, json.JSONDecodeError):
        return {}
    return {k: v for k, v in data.items() if not str(k).startswith("_")}

logger = logging.getLogger(__name__)

Notify = Callable[[str], Awaitable[None]]

CATEGORY_LABELS = {
    "coaster": "montagne russe",
    "thrill": "sensations",
    "water": "aquatique",
    "family": "familiale",
    "kids": "enfants",
    "show": "spectacle",
    "walkthrough": "walkthrough",
    "transport": "transport",
}

PHASE_LABELS = {
    "before_open": "Avant ouverture",
    "opening": "Ouverture",
    "peak": "Heures pleines (11h–15h)",
    "afternoon": "Après-midi",
    "closing": "Dernière heure",
    "closed": "Fermé",
    "unknown": "Horaires inconnus",
}


def esc(text: str) -> str:
    return text.replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")


class KairosService:
    def __init__(
        self,
        settings: Settings,
        store: Store,
        fetcher: QueueTimesFetcher,
        engine: Engine,
        hours: ParkHours,
        alerter: Alerter,
        parks: list[dict[str, Any]],
    ) -> None:
        self.settings = settings
        self.store = store
        self.fetcher = fetcher
        self.engine = engine
        self.hours = hours
        self.alerter = alerter
        self.parks = parks
        self.parks_by_key = {p["key"]: p for p in parks}
        self.notifier: Notify | None = None
        self._lock = asyncio.Lock()
        self._first_poll = True
        self.last_poll: datetime | None = None
        self.last_error: str | None = None
        # Fiches attraction : descriptions, crédits photo et dossier d'images.
        self.descriptions = _load_map(settings.resolve(settings.descriptions_config))
        self.credits = _load_map(settings.resolve(settings.credits_config))
        self.images_dir = settings.resolve(settings.rides_images_dir)

    # ------------------------------------------------------------ info --- #

    @property
    def mode(self) -> str:
        return self.store.get_state("mode", self.settings.mode)

    def park_name(self, key: str) -> str:
        return self.parks_by_key.get(key, {}).get("name", key)

    def current_zone(self) -> str | None:
        return self.store.get_state("current_zone")

    def phase_label(self, now: datetime | None = None) -> str:
        return PHASE_LABELS.get(self.hours.phase(now or utcnow()), "—")

    def hours_warning(self) -> str | None:
        """Rappel « mets à jour les horaires », ou None si tout est à jour."""
        return self.hours.staleness_warning(utcnow())

    def rides(self) -> list[Ride]:
        return self.store.rides()

    def resolve_ride(self, query: str) -> Ride | None:
        q = (query or "").strip().lower()
        if not q:
            return None
        rides = self.store.rides(include_hidden=True)
        for ride in rides:
            if ride.name.lower() == q:
                return ride
        matches = [ride for ride in rides if q in ride.name.lower()]
        if len(matches) == 1:
            return matches[0]
        return matches[0] if matches else None

    def resolve_zone(self, query: str) -> str | None:
        zones = list(self.engine.zones.coords.keys())
        q = (query or "").strip().lower()
        for zone in zones:
            if zone.lower() == q:
                return zone
        for zone in zones:
            if q and q in zone.lower():
                return zone
        return None

    def zones(self) -> list[str]:
        return list(self.engine.zones.coords.keys())

    # ------------------------------------------------------- fiche ride --- #

    def ride_image_file(self, ride_id: int) -> Path | None:
        for ext in IMAGE_EXTS:
            candidate = self.images_dir / f"{ride_id}{ext}"
            if candidate.exists():
                return candidate
        return None

    def ride_image_url(self, ride_id: int) -> str | None:
        path = self.ride_image_file(ride_id)
        return f"/static/{self.images_dir.name}/{path.name}" if path else None

    def hourly_profile(
        self, ride_id: int, now: datetime | None = None, days: int = 30
    ) -> tuple[list[dict[str, Any]], int | None]:
        """Attente moyenne par heure locale, depuis l'historique. Renvoie (profil, meilleure heure)."""
        now = now or utcnow()
        rows = self.store.ride_open_waits(ride_id, now - timedelta(days=days))
        buckets: dict[int, list[int]] = {}
        for ts, wait in rows:
            buckets.setdefault(self.hours.local(ts).hour, []).append(wait)
        profile = [
            {"hour": hour, "avg": round(sum(waits) / len(waits)), "n": len(waits)}
            for hour, waits in sorted(buckets.items())
        ]
        best = min(profile, key=lambda p: p["avg"])["hour"] if profile else None
        return profile, best

    def ride_detail(self, ride_id: int) -> dict[str, Any] | None:
        """Fiche complète d'une attraction (pour le dashboard)."""
        ride = next((r for r in self.store.rides(include_hidden=True) if r.id == ride_id), None)
        if ride is None:
            return None
        now = utcnow()
        snap = self.store.latest().get(ride_id)
        profile, best_hour = self.hourly_profile(ride_id, now)
        trend, trend_label = self.engine.trend(ride_id, now)
        return {
            "id": ride.id,
            "name": ride.name,
            "park": ride.park,
            "park_name": self.park_name(ride.park),
            "zone": ride.zone,
            "category": ride.category,
            "priority": ride.priority,
            "is_open": bool(snap and snap["is_open"]),
            "wait_minutes": snap["wait"] if snap else None,
            "trend": trend,
            "trend_label": trend_label,
            "image": self.ride_image_url(ride.id),
            "description": self.descriptions.get(str(ride.id)),
            "credit": self.credits.get(str(ride.id)),
            "hourly": profile,
            "best_hour": best_hour,
        }

    # ---------------------------------------------------------- polling --- #

    async def poll_once(self) -> None:
        async with self._lock:
            try:
                await self._poll_once()
                self.last_error = None
            except Exception as exc:  # noqa: BLE001 - on ne veut jamais tuer la boucle
                self.last_error = str(exc)
                logger.exception("Erreur pendant le polling")

    async def _poll_once(self) -> None:
        now = utcnow()
        if self.settings.skip_when_closed and self.hours.phase(now) in ("closed", "before_open"):
            logger.debug("Parc fermé : poll ignoré (aucune donnée inutile stockée).")
            return

        data = await self.fetcher.fetch_all()

        # On collecte aussi les attractions masquées (historique conservé, réaffichage possible).
        known = {ride.id: ride for ride in self.store.rides(include_hidden=True)}
        previous = self.store.latest()
        rows: list[tuple[int, datetime, int, bool]] = []
        fetched = 0
        for raw_rides in data.values():
            for raw in raw_rides:
                if raw.ride_id not in known:
                    continue
                fetched += 1
                if raw.is_open:
                    rows.append((raw.ride_id, now, raw.wait_minutes, True))
                else:
                    # On ne stocke que la transition ouvert -> fermé, pas chaque poll fermé
                    # (les attractions fermées en continu n'apportent aucune information).
                    prev = previous.get(raw.ride_id)
                    if prev is not None and prev["is_open"]:
                        rows.append((raw.ride_id, now, raw.wait_minutes, False))

        self.last_poll = now
        if not rows:
            logger.debug("Aucun changement d'état à stocker.")
            return

        self.store.add_snapshots(rows)

        statuses = self.engine.statuses(self.current_zone(), now)

        if self._first_poll:
            self.alerter.prime(statuses)
            self._first_poll = False
        else:
            alerts = self.alerter.evaluate(statuses)
            if self.mode == "trip" and alerts and self.notifier:
                for message in self.format_alerts(alerts):
                    await self.notifier(message)

        if self.settings.history_retention_days > 0:
            cutoff = now - timedelta(days=self.settings.history_retention_days)
            self.store.prune_snapshots(cutoff)

        logger.info("Poll OK : %d attractions lues, %d snapshots enregistrés", fetched, len(rows))

    # --------------------------------------------------------- timeline --- #

    async def check_timeline(self) -> None:
        if self.mode != "trip" or not self.notifier:
            return
        now = utcnow()
        phase = self.hours.phase(now)
        if phase in ("closed", "before_open", "unknown"):
            return
        today = self.hours.local(now).date().isoformat()

        if not self.store.get_state(f"briefing:{today}"):
            await self.notifier(self.format_briefing())
            self.store.set_state(f"briefing:{today}", True)

        # Rappel de fermeture parc par parc (PortAventura et Ferrari Land ne ferment pas pareil).
        for park in (self.hours.parks() or [None]):
            if self.hours.phase(now, park) not in ("opening", "peak", "afternoon", "closing"):
                continue
            minutes_to_close = self.hours.minutes_to_close(now, park)
            if minutes_to_close is None or not (0 < minutes_to_close <= 60):
                continue
            key = f"closing:{park or 'global'}:{today}"
            if self.store.get_state(key):
                continue
            await self.notifier(self.format_closing_reminder(minutes_to_close, park))
            self.store.set_state(key, True)

    # --------------------------------------------------------- messages --- #

    def format_alert(self, alert) -> str:
        icon = "🔓" if alert.kind == "reopened" else "⚡"
        if alert.baseline_median is not None:
            context = f"habituellement {alert.baseline_median} min à cette heure"
        else:
            context = f"seuil {alert.threshold} min"
        return (
            f"{icon} <b>{esc(alert.ride.name)}</b> — {alert.wait_minutes} min · {esc(alert.ride.zone)}\n"
            f"<i>{context} — c'est le moment, fonce !</i>"
        )

    def format_alerts(self, alerts) -> list[str]:
        """Regroupe les alertes d'un même poll par parc.

        Un poll peut détecter plusieurs franchissements à la fois (souvent des
        réouvertures groupées, ex. l'ouverture de Ferrari Land). Sans regroupement,
        cela envoie une rafale de messages ; ici on n'en envoie qu'un par parc.
        """
        if len(alerts) == 1:
            return [self.format_alert(alerts[0])]

        by_park: dict[str, list] = {}
        for alert in alerts:
            by_park.setdefault(alert.ride.park, []).append(alert)

        messages: list[str] = []
        for park, group in by_park.items():
            kinds = {a.kind for a in group}
            icon = "🔓" if kinds == {"reopened"} else ("⚡" if kinds == {"threshold"} else "🔔")
            verb = "attraction se libère" if len(group) == 1 else "attractions se libèrent"
            lines = [f"{icon} <b>{len(group)} {verb} · {esc(self.park_name(park))}</b>"]
            for alert in group:
                mark = "🔓" if alert.kind == "reopened" else "⚡"
                suffix = f" (hab. {alert.baseline_median} min)" if alert.baseline_median is not None else ""
                lines.append(
                    f"{mark} <b>{esc(alert.ride.name)}</b> — {alert.wait_minutes} min{suffix} · {esc(alert.ride.zone)}"
                )
            lines.append("<i>C'est le moment, foncez !</i>")
            messages.append("\n".join(lines))
        return messages

    def format_recos(self, limit: int = 3) -> str:
        recos = self.engine.recommend(self.current_zone(), limit=limit)
        if not recos:
            return "Aucune attraction ouverte pour l'instant. Le parc est peut-être fermé."
        lines = ["🎯 <b>Où aller maintenant</b>"]
        zone = self.current_zone()
        lines.append(f"<i>Depuis : {esc(zone) if zone else 'ta position inconnue'}</i>")
        lines.append("")
        medals = ["🥇", "🥈", "🥉"]
        for i, reco in enumerate(recos):
            prefix = medals[i] if i < len(medals) else "•"
            arrow = trend_arrow(reco.trend_label)
            wait = f"{reco.wait_minutes} min" if reco.wait_minutes is not None else "?"
            lines.append(
                f"{prefix} <b>{esc(reco.ride.name)}</b> — {wait} {arrow} · {esc(reco.ride.zone)}"
            )
            if reco.reason:
                lines.append(f"    <i>{esc(reco.reason)}</i>")
        return "\n".join(lines)

    def format_status_table(self, park_key: str | None = None) -> str:
        statuses = self.engine.statuses(self.current_zone())
        if park_key:
            statuses = [s for s in statuses if s.ride.park == park_key]
        open_statuses = [s for s in statuses if s.is_open]
        if not open_statuses:
            return "Aucune attraction ouverte pour l'instant."

        park_keys = [park_key] if park_key else [p["key"] for p in self.parks]
        lines: list[str] = []
        for key in park_keys:
            park_statuses = [s for s in open_statuses if s.ride.park == key]
            if not park_statuses:
                continue
            lines.append(f"<b>{esc(self.park_name(key))}</b>")
            for status in sorted(park_statuses, key=lambda s: (s.ride.zone, -(s.wait_minutes or 0))):
                wait = status.wait_minutes or 0
                mark = "✅" if status.done else ("🔥" if status.ride.priority == 3 else "•")
                arrow = trend_arrow(status.trend_label)
                lines.append(
                    f"{mark} {esc(status.ride.name)} — <b>{wait} min</b> {arrow} · {esc(status.ride.zone)}"
                )
            lines.append("")
        return "\n".join(lines).strip()

    def format_briefing(self) -> str:
        now = utcnow()
        local = self.hours.local(now)
        hour = local.strftime("%H:%M")
        recos = self.engine.recommend(self.current_zone(), limit=3)
        lines = [f"☀️ <b>Bonjour !</b> Il est {hour}.", ""]
        if recos:
            lines.append("Pour commencer, visez :")
            for reco in recos:
                wait = f"{reco.wait_minutes} min" if reco.wait_minutes is not None else "?"
                lines.append(f"• <b>{esc(reco.ride.name)}</b> — {wait} · {esc(reco.ride.zone)}")
        lines.append("")
        lines.append("<i>Astuce : les grosses attractions sont les plus calmes à l'ouverture et en fin de journée.</i>")
        warning = self.hours.staleness_warning(now)
        if warning:
            lines.append("")
            lines.append(f"⚠️ <i>{esc(warning)}</i>")
        return "\n".join(lines)

    def format_closing_reminder(self, minutes_to_close: float, park: str | None = None) -> str:
        statuses = self.engine.statuses(self.current_zone())
        if park:
            statuses = [s for s in statuses if s.ride.park == park]
        remaining = [s for s in statuses if not s.done and s.ride.priority >= 3]
        label = esc(self.park_name(park)) if park else "Le parc"
        lines = [f"⏳ <b>{label} ferme dans {int(minutes_to_close)} min.</b>"]
        if remaining:
            names = ", ".join(esc(s.ride.name) for s in remaining[:6])
            lines.append(f"Il reste : {names}")
        else:
            lines.append("Vous avez coché toutes les priorités, bravo ! 🎉")
        lines.append("<i>Dernière vague sur les grosses attractions.</i>")
        return "\n".join(lines)

    # -------------------------------------------------------- dashboard --- #

    @staticmethod
    def _reco_payload(r) -> dict[str, Any]:
        return {
            "id": r.ride.id,
            "name": r.ride.name,
            "zone": r.ride.zone,
            "wait_minutes": r.wait_minutes,
            "trend_label": r.trend_label,
            "trend": r.trend_delta,
            "travel_minutes": r.travel_minutes,
            "score": r.score,
            "reason": r.reason,
        }

    def state_payload(self) -> dict[str, Any]:
        now = utcnow()
        statuses = self.engine.statuses(self.current_zone(), now)
        recos = self.engine.recommend(self.current_zone(), limit=5, now=now)
        coaster_recos = self.engine.recommend(
            self.current_zone(), limit=5, now=now, categories={"coaster"}
        )

        rides_payload: list[dict[str, Any]] = []
        for status in statuses:
            rides_payload.append(
                {
                    "id": status.ride.id,
                    "name": status.ride.name,
                    "park": status.ride.park,
                    "park_name": self.park_name(status.ride.park),
                    "zone": status.ride.zone,
                    "category": status.ride.category,
                    "priority": status.ride.priority,
                    "is_open": status.is_open,
                    "wait_minutes": status.wait_minutes,
                    "trend_label": status.trend_label,
                    "trend": status.trend,
                    "done": status.done,
                    "score": status.score if math.isfinite(status.score) else None,
                    "travel_minutes": round(status.travel_minutes, 1),
                    "updated": iso(status.updated) if status.updated else None,
                    "image": self.ride_image_url(status.ride.id),
                }
            )

        return {
            "generated_at": iso(now),
            "phase": self.hours.phase(now),
            "minutes_to_close": self.hours.minutes_to_close(now),
            "hours_warning": self.hours_warning(),
            "alert_threshold": self.alerter.default_threshold(),
            "mode": self.mode,
            "current_zone": self.current_zone(),
            "zones": self.zones(),
            "parks": [
                {
                    "key": p["key"],
                    "name": p["name"],
                    "phase": self.hours.phase(now, p["key"]),
                    "minutes_to_close": self.hours.minutes_to_close(now, p["key"]),
                }
                for p in self.parks
            ],
            "rides": rides_payload,
            "recommendations": [self._reco_payload(r) for r in recos],
            "coaster_recommendations": [self._reco_payload(r) for r in coaster_recos],
            "last_poll": iso(self.last_poll) if self.last_poll else None,
            "last_error": self.last_error,
            "attribution": {
                "text": self.settings.attribution_text,
                "url": self.settings.attribution_url,
            },
        }
