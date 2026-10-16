"""Modèles de données de Kairos."""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime

# Catégories d'attraction utilisées par le moteur (bonus par phase de journée).
CATEGORIES = (
    "coaster",
    "thrill",
    "water",
    "family",
    "kids",
    "show",
    "walkthrough",
    "transport",
)

TREND_LABELS = ("rising", "falling", "stable")


@dataclass(frozen=True)
class Ride:
    id: int
    name: str
    park: str
    zone: str
    category: str
    priority: int = 2


@dataclass
class RideStatus:
    """État courant d'une attraction, enrichi par le moteur."""

    ride: Ride
    is_open: bool = False
    wait_minutes: int | None = None
    updated: datetime | None = None
    done: bool = False
    trend: float = 0.0            # minutes gagnées/perdues sur 30 min
    trend_label: str = "stable"   # rising | falling | stable
    travel_minutes: float = 0.0
    score: float = 0.0
    phase: str = "unknown"        # phase du parc de l'attraction
    minutes_to_close: float | None = None


@dataclass
class Recommendation:
    ride: Ride
    wait_minutes: int | None
    trend_label: str
    trend_delta: float
    travel_minutes: float
    score: float
    reason: str


@dataclass
class Alert:
    ride: Ride
    wait_minutes: int
    threshold: int
    kind: str  # threshold | reopened


@dataclass
class RawRide:
    """Donnée brute issue de queue-times.com."""

    ride_id: int
    is_open: bool
    wait_minutes: int
    updated: datetime | None = None
