"""Évaluation des alertes avec hystérésis et anti-spam."""

from __future__ import annotations

from datetime import datetime, timezone

from .config import Settings
from .models import Alert, RideStatus
from .store import Store


class Alerter:
    def __init__(self, store: Store, settings: Settings) -> None:
        self.store = store
        self.settings = settings
        # États observés au cycle précédent (mémoire process).
        self._previous_wait: dict[int, int] = {}
        self._previous_open: dict[int, bool] = {}

    # ---------------------------------------------------- seuils config --- #

    def default_threshold(self) -> int:
        return int(
            self.store.get_state(
                "alert_threshold_minutes", self.settings.alert_default_threshold_minutes
            )
        )

    def threshold_for(self, ride_id: int) -> int | None:
        overrides = self.store.get_state("alert_threshold_overrides", {}) or {}
        value = overrides.get(str(ride_id), overrides.get(ride_id))
        if value is None:
            return self.default_threshold()
        return int(value)

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

    # -------------------------------------------------------- évaluation --- #

    def prime(self, statuses: list[RideStatus]) -> None:
        """Initialise la mémoire d'observation sans émettre d'alerte (démarrage)."""
        for status in statuses:
            self._previous_open[status.ride.id] = status.is_open
            if status.is_open:
                self._previous_wait[status.ride.id] = status.wait_minutes or 0

    def evaluate(self, statuses: list[RideStatus]) -> list[Alert]:
        """Détecte les franchissements de seuil et les réouvertures."""
        now = datetime.now(timezone.utc)
        cooldown = self.settings.alert_cooldown_minutes
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

            threshold = self.threshold_for(ride_id)
            if threshold is None:
                continue
            wait = status.wait_minutes or 0
            if wait >= threshold:
                continue

            just_crossed = previous_wait is None or previous_wait >= threshold
            just_reopened = previous_open is False
            if not (just_crossed or just_reopened):
                continue

            if self._in_cooldown(ride_id, "threshold", cooldown, now):
                continue

            kind = "reopened" if just_reopened else "threshold"
            alerts.append(Alert(ride=status.ride, wait_minutes=wait, threshold=threshold, kind=kind))
            self.store.log_alert(ride_id, "threshold", wait)

        return alerts

    def _in_cooldown(self, ride_id: int, kind: str, cooldown_minutes: int, now: datetime) -> bool:
        last = self.store.last_alert_ts(ride_id, kind)
        if last is None:
            return False
        return (now - last).total_seconds() < cooldown_minutes * 60
