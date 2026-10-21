"""Évaluation des alertes.

Alerting **adaptatif** : pour chaque attraction et chaque heure de la journée, on
apprend son attente habituelle sur les derniers jours. On alerte quand l'attente
passe sous un percentile bas *de cette heure*, et seulement si l'attraction est
normalement assez demandée à ce moment-là (sinon « 0 min » n'est pas une info).

Repli : seuil manuel (override ``/seuil``), sinon seuil global, quand l'historique
est insuffisant (démarrage à froid).
"""

from __future__ import annotations

import time
from datetime import datetime, timedelta, timezone
from statistics import median as _median
from zoneinfo import ZoneInfo

from .config import Settings
from .models import Alert, RideStatus
from .store import Store

# Sensibilité -> percentile de la baseline + médiane minimale pour alerter.
SENSITIVITIES: dict[str, dict[str, float]] = {
    "prudent": {"pct": 0.10, "min_median": 15},
    "normal": {"pct": 0.20, "min_median": 10},
    "agressif": {"pct": 0.30, "min_median": 8},
}
SENSITIVITY_LEVELS = tuple(SENSITIVITIES)
DEFAULT_SENSITIVITY = "agressif"

BASELINE_DAYS = 21
BASELINE_MIN_SAMPLES = 8
BASELINE_TTL_SECONDS = 3600


def _percentile(values: list[int], pct: float) -> float:
    ordered = sorted(values)
    k = (len(ordered) - 1) * pct
    lo = int(k)
    hi = min(lo + 1, len(ordered) - 1)
    return ordered[lo] + (ordered[hi] - ordered[lo]) * (k - lo)


def adaptive_cut(waits: list[int], sensitivity: str) -> tuple[float | None, int]:
    """Seuil adaptatif et médiane pour un échantillon d'attentes.

    Renvoie ``(None, médiane)`` quand l'attraction est déjà peu demandée à cette
    heure : dans ce cas, une attente basse n'est pas une information.
    """
    params = SENSITIVITIES.get(sensitivity, SENSITIVITIES[DEFAULT_SENSITIVITY])
    typical = int(_median(waits))
    if typical < params["min_median"]:
        return None, typical
    return _percentile(waits, params["pct"]), typical


class Alerter:
    def __init__(self, store: Store, settings: Settings) -> None:
        self.store = store
        self.settings = settings
        self.tz = ZoneInfo(settings.timezone)
        # États observés au cycle précédent (mémoire process).
        self._previous_wait: dict[int, int] = {}
        self._previous_open: dict[int, bool] = {}
        # Baseline adaptative, reconstruite au plus une fois par heure.
        self._baseline_hour: dict[tuple[int, int], list[int]] = {}
        self._baseline_all: dict[int, list[int]] = {}
        self._baseline_at: float | None = None

    # ---------------------------------------------------- seuils manuels --- #

    def default_threshold(self) -> int:
        return int(
            self.store.get_state(
                "alert_threshold_minutes", self.settings.alert_default_threshold_minutes
            )
        )

    def threshold_for(self, ride_id: int) -> int:
        override = self.ride_threshold_override(ride_id)
        return override if override is not None else self.default_threshold()

    def ride_threshold_override(self, ride_id: int) -> int | None:
        overrides = self.store.get_state("alert_threshold_overrides", {}) or {}
        value = overrides.get(str(ride_id), overrides.get(ride_id))
        return int(value) if value is not None else None

    def set_default_threshold(self, minutes: int) -> None:
        self.store.set_state("alert_threshold_minutes", int(minutes))

    def set_ride_threshold(self, ride_id: int, minutes: int | None) -> None:
        overrides = self.store.get_state("alert_threshold_overrides", {}) or {}
        if minutes is None:
            overrides.pop(str(ride_id), None)
            overrides.pop(ride_id, None)
        else:
            overrides[str(ride_id)] = int(minutes)
        self.store.set_state("alert_threshold_overrides", overrides)

    # ---------------------------------------------------------- portée --- #

    def alert_scope(self) -> str:
        """``all`` (toutes attractions) ou ``coasters`` (montagnes russes uniquement)."""
        return self.store.get_state("alert_scope", "all") or "all"

    def set_alert_scope(self, scope: str) -> None:
        self.store.set_state("alert_scope", "coasters" if scope == "coasters" else "all")

    # ------------------------------------------------------ sensibilité --- #

    def alert_sensitivity(self) -> str:
        value = self.store.get_state("alert_sensitivity", DEFAULT_SENSITIVITY)
        return value if value in SENSITIVITIES else DEFAULT_SENSITIVITY

    def set_alert_sensitivity(self, name: str) -> None:
        if name in SENSITIVITIES:
            self.store.set_state("alert_sensitivity", name)

    # --------------------------------------------------------- baseline --- #

    def _ensure_baseline(self) -> None:
        now = time.monotonic()
        if self._baseline_at is not None and now - self._baseline_at < BASELINE_TTL_SECONDS:
            return
        since = datetime.now(timezone.utc) - timedelta(days=BASELINE_DAYS)
        data = self.store.open_waits_by_ride(since)
        by_hour: dict[tuple[int, int], list[int]] = {}
        by_all: dict[int, list[int]] = {}
        for ride_id, points in data.items():
            all_waits: list[int] = []
            for ts, wait in points:
                all_waits.append(wait)
                hour = ts.astimezone(self.tz).hour
                by_hour.setdefault((ride_id, hour), []).append(wait)
            if len(all_waits) >= BASELINE_MIN_SAMPLES:
                by_all[ride_id] = all_waits
        self._baseline_hour = {
            key: waits for key, waits in by_hour.items() if len(waits) >= BASELINE_MIN_SAMPLES
        }
        self._baseline_all = by_all
        self._baseline_at = now

    def _cut(self, ride_id: int, now: datetime) -> tuple[float | None, int | None]:
        """Renvoie (seuil, médiane habituelle). Seuil None = pas d'alerte."""
        override = self.ride_threshold_override(ride_id)
        if override is not None:
            return float(override), None

        self._ensure_baseline()
        hour = now.astimezone(self.tz).hour
        waits = self._baseline_hour.get((ride_id, hour)) or self._baseline_all.get(ride_id)
        if not waits:
            return float(self.default_threshold()), None  # démarrage à froid
        return adaptive_cut(waits, self.alert_sensitivity())

    # -------------------------------------------------------- évaluation --- #

    def prime(self, statuses: list[RideStatus]) -> None:
        """Initialise la mémoire d'observation sans émettre d'alerte (démarrage)."""
        for status in statuses:
            self._previous_open[status.ride.id] = status.is_open
            if status.is_open:
                self._previous_wait[status.ride.id] = status.wait_minutes or 0

    def evaluate(
        self, statuses: list[RideStatus], now: datetime | None = None
    ) -> list[Alert]:
        """Détecte les franchissements de seuil et les réouvertures."""
        now = now or datetime.now(timezone.utc)
        cooldown = self.settings.alert_cooldown_minutes
        coasters_only = self.alert_scope() == "coasters"
        alerts: list[Alert] = []

        for status in statuses:
            ride_id = status.ride.id
            previous_wait = self._previous_wait.get(ride_id)
            previous_open = self._previous_open.get(ride_id)

            # Mise à jour de la mémoire d'observation.
            self._previous_open[ride_id] = status.is_open
            if status.is_open:
                self._previous_wait[ride_id] = status.wait_minutes or 0

            if status.done or not status.is_open:
                continue
            if status.ride.priority < 2:
                continue
            if coasters_only and status.ride.category != "coaster":
                continue

            cut, typical = self._cut(ride_id, now)
            if cut is None:
                continue

            wait = status.wait_minutes or 0
            if wait >= cut:
                continue

            just_crossed = previous_wait is None or previous_wait >= cut
            just_reopened = previous_open is False
            if not (just_crossed or just_reopened):
                continue

            # Cooldown par attraction, toutes catégories confondues.
            if self._in_cooldown(ride_id, cooldown, now):
                continue

            kind = "reopened" if just_reopened else "threshold"
            alerts.append(
                Alert(
                    ride=status.ride,
                    wait_minutes=wait,
                    threshold=int(round(cut)),
                    kind=kind,
                    baseline_median=typical,
                )
            )
            self.store.log_alert(ride_id, kind, wait)

        return alerts

    def _in_cooldown(self, ride_id: int, cooldown_minutes: int, now: datetime) -> bool:
        last = self.store.last_alert_ts(ride_id)
        if last is None:
            return False
        return (now - last).total_seconds() < cooldown_minutes * 60
