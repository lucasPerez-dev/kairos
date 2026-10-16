"""Moteur de recommandation : score, tendance et phases de la journée."""

from __future__ import annotations

import math
from datetime import datetime, timezone

from .models import Recommendation, Ride, RideStatus
from .park_hours import ParkHours
from .store import Store

# Poids d'un niveau de priorité, exprimé en "minutes équivalentes".
PRIORITY_WEIGHT = 22.0

# Trajet par défaut (minutes) quand la zone est inconnue/absente.
DEFAULT_TRAVEL_MINUTES = 4.0

# Bonus/malus par phase de journée et par catégorie (en minutes équivalentes).
PHASE_BONUS: dict[str, dict[str, float]] = {
    "opening": {
        "coaster": 25, "thrill": 18, "water": 6, "family": 6,
        "kids": 4, "show": 0, "walkthrough": 2, "transport": 4,
    },
    "peak": {
        "coaster": -12, "thrill": -6, "water": 16, "show": 22,
        "walkthrough": 14, "family": 8, "kids": 8, "transport": 8,
    },
    "afternoon": {
        "coaster": 4, "thrill": 4, "water": 4, "show": 6,
        "walkthrough": 4, "family": 4, "kids": 4, "transport": 4,
    },
    "closing": {
        "coaster": 18, "thrill": 14, "water": 0, "show": 0,
        "walkthrough": 2, "family": 6, "kids": 6, "transport": 4,
    },
    "before_open": {"coaster": 10, "thrill": 8},
    "after_close": {},
    "closed": {},
    "unknown": {},
}

TREND_RISING_THRESHOLD = 3.0    # minutes / 30 min
TREND_FALLING_THRESHOLD = -3.0


class Zones:
    """Modèle grossier de marche entre zones."""

    def __init__(self, config: dict) -> None:
        self.scale = float(config.get("layout_scale_meters", 9))
        self.speed = float(config.get("walk_speed_m_per_min", 70)) or 70.0
        self.coords: dict[str, tuple[float, float]] = {
            name: (float(xy[0]), float(xy[1]))
            for name, xy in (config.get("zones") or {}).items()
        }

    def travel_minutes(self, origin: str | None, destination: str | None) -> float:
        if not origin or not destination or origin == destination:
            return 0.0
        a = self.coords.get(origin)
        b = self.coords.get(destination)
        if a is None or b is None:
            return DEFAULT_TRAVEL_MINUTES
        return math.dist(a, b) * self.scale / self.speed


class Engine:
    def __init__(self, store: Store, hours: ParkHours, zones: Zones) -> None:
        self.store = store
        self.hours = hours
        self.zones = zones

    # --------------------------------------------------------- tendance --- #

    def trend(self, ride_id: int, now: datetime | None = None) -> tuple[float, str]:
        """Renvoie (delta sur 30 min, label) pour une attraction."""
        now = now or datetime.now(timezone.utc)
        points = self.store.history(ride_id, minutes=45, now=now)
        if len(points) < 3:
            return 0.0, "stable"
        delta = _slope_per_minute(points) * 30.0
        if delta >= TREND_RISING_THRESHOLD:
            label = "rising"
        elif delta <= TREND_FALLING_THRESHOLD:
            label = "falling"
        else:
            label = "stable"
        return round(delta, 1), label

    # ----------------------------------------------------------- score --- #

    def score(self, status: RideStatus, phase: str, minutes_to_close: float | None) -> float:
        if not status.is_open:
            return float("-inf")
        ride = status.ride
        score = ride.priority * PRIORITY_WEIGHT
        score += PHASE_BONUS.get(phase, {}).get(ride.category, 0.0)
        score -= status.wait_minutes or 0
        score -= status.travel_minutes

        delta = status.trend
        if delta <= TREND_FALLING_THRESHOLD:
            score += min(-delta * 0.6, 14.0)      # la file se vide → on y va
        elif delta >= TREND_RISING_THRESHOLD:
            score -= min(delta * 0.5, 12.0)       # la file grimpe → on temporise

        if minutes_to_close is not None and minutes_to_close < 150 and ride.priority >= 3:
            score += (150 - max(minutes_to_close, 0)) / 150 * 14.0

        return round(score, 2)

    # ------------------------------------------------------- inventaire --- #

    def statuses(
        self,
        current_zone: str | None = None,
        now: datetime | None = None,
        with_trend: bool = True,
    ) -> list[RideStatus]:
        now = now or datetime.now(timezone.utc)
        latest = self.store.latest()
        done = self.store.done_ids()
        phases: dict[str, tuple[str, float | None]] = {}

        out: list[RideStatus] = []
        for ride in self.store.rides():
            # Phase et fermeture propres au parc de l'attraction (horaires distincts).
            if ride.park not in phases:
                phases[ride.park] = (
                    self.hours.phase(now, ride.park),
                    self.hours.minutes_to_close(now, ride.park),
                )
            phase, minutes_to_close = phases[ride.park]
            snap = latest.get(ride.id)
            status = RideStatus(
                ride=ride,
                is_open=bool(snap and snap["is_open"]),
                wait_minutes=snap["wait"] if snap else None,
                updated=snap["ts"] if snap else None,
                done=ride.id in done,
                travel_minutes=self.zones.travel_minutes(current_zone, ride.zone),
                phase=phase,
                minutes_to_close=minutes_to_close,
            )
            if with_trend:
                status.trend, status.trend_label = self.trend(ride.id, now)
            status.score = self.score(status, phase, minutes_to_close)
            out.append(status)
        return out

    def recommend(
        self,
        current_zone: str | None = None,
        limit: int = 3,
        now: datetime | None = None,
    ) -> list[Recommendation]:
        now = now or datetime.now(timezone.utc)

        candidates = [
            st for st in self.statuses(current_zone, now)
            if st.is_open and not st.done
        ]
        candidates.sort(key=lambda st: st.score, reverse=True)

        return [
            Recommendation(
                ride=st.ride,
                wait_minutes=st.wait_minutes,
                trend_label=st.trend_label,
                trend_delta=st.trend,
                travel_minutes=round(st.travel_minutes, 1),
                score=st.score,
                reason=describe(st, st.phase, st.minutes_to_close, current_zone),
            )
            for st in candidates[:limit]
        ]


# -------------------------------------------------------------- helpers --- #


def _slope_per_minute(points: list[tuple[datetime, int, bool]]) -> float:
    """Pente d'une régression linéaire simple (minutes d'attente par minute)."""
    t0 = points[0][0]
    xs = [(ts - t0).total_seconds() / 60 for ts, _, _ in points]
    ys = [wait for _, wait, _ in points]
    n = len(xs)
    mean_x = sum(xs) / n
    mean_y = sum(ys) / n
    denom = sum((x - mean_x) ** 2 for x in xs)
    if denom == 0:
        return 0.0
    return sum((x - mean_x) * (y - mean_y) for x, y in zip(xs, ys)) / denom


def trend_arrow(label: str) -> str:
    return {"rising": "↗", "falling": "↘", "stable": "→"}.get(label, "→")


def describe(
    status: RideStatus,
    phase: str,
    minutes_to_close: float | None,
    current_zone: str | None,
) -> str:
    parts: list[str] = []

    if status.trend_label == "falling":
        parts.append(f"la file se vide ({status.trend:+.0f} min/30 min)")
    elif status.trend_label == "rising":
        parts.append(f"la file grimpe ({status.trend:+.0f} min/30 min)")

    if current_zone and status.travel_minutes >= 1:
        parts.append(f"{status.travel_minutes:.0f} min de marche")
    elif current_zone:
        parts.append("dans ta zone")

    if phase == "closing" and minutes_to_close is not None:
        parts.append(f"ferme dans {int(minutes_to_close)} min")
    elif phase == "opening":
        parts.append("profite de l'ouverture")

    return " · ".join(parts) if parts else "bon créneau"
