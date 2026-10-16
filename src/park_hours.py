"""Horaires d'ouverture et phases de la journée.

Support de plusieurs parcs ayant des horaires distincts (PortAventura Park,
Ferrari Land…). La résolution, par parc et par date, suit cet ordre :

    ``closed`` (date fermée) > ``days[date]`` > ``weekly[jour]`` > ``default``

Une fermeture égale ou antérieure à l'ouverture est interprétée comme « après
minuit » (ex. ``close: "00:00"`` pour une nocturne jusqu'à minuit).

L'ancien format « plat » (``default``/``days`` à la racine, sans ``parks``) reste
accepté : il s'applique alors à tous les parcs.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date, datetime, time, timedelta
from zoneinfo import ZoneInfo

Phase = str  # before_open | opening | peak | afternoon | closing | closed | unknown

PEAK_START_HOUR = 11
PEAK_END_HOUR = 15
EDGE_WINDOW_MINUTES = 90  # fenêtre "ouverture" / "fermeture"

WEEKDAY_KEYS = ("mon", "tue", "wed", "thu", "fri", "sat", "sun")


def _parse_range(value: object) -> tuple[time, time] | None:
    if not value:
        return None
    if isinstance(value, dict) and value.get("closed"):
        return None
    try:
        return time.fromisoformat(value["open"]), time.fromisoformat(value["close"])  # type: ignore[index]
    except (KeyError, ValueError, TypeError):
        return None


@dataclass(frozen=True)
class DayHours:
    open: time
    close: time
    estimated: bool = False


class _Schedule:
    """Planning d'un parc : récurrence hebdo + exceptions par date."""

    def __init__(self, config: dict) -> None:
        self.default = config.get("default")
        self.weekly = {
            str(key).lower()[:3]: value for key, value in (config.get("weekly") or {}).items()
        }
        self.days = config.get("days") or {}
        self.closed = {str(d) for d in (config.get("closed") or [])}

    def for_date(self, day: date) -> DayHours | None:
        iso = day.isoformat()
        if iso in self.closed:
            return None
        entry = self.days.get(iso)
        if entry is None:
            entry = self.weekly.get(WEEKDAY_KEYS[day.weekday()])
        if entry is None:
            entry = self.default
        parsed = _parse_range(entry)
        if parsed is None:
            return None
        return DayHours(open=parsed[0], close=parsed[1], estimated=bool(
            isinstance(entry, dict) and entry.get("estimated", False)
        ))


class ParkHours:
    def __init__(self, config: dict, timezone: str) -> None:
        self.tz = ZoneInfo(timezone)
        parks = config.get("parks")
        if parks:
            self._schedules: dict[str, _Schedule] = {
                key: _Schedule(sub or {}) for key, sub in parks.items()
            }
        else:
            # Format plat historique : un seul planning, appliqué à tous les parcs.
            self._schedules = {"*": _Schedule(config)}

    # ---------------------------------------------------------- résolution --- #

    def parks(self) -> list[str]:
        return [key for key in self._schedules if key != "*"]

    def _schedule(self, park: str | None) -> _Schedule | None:
        if park is not None and park in self._schedules:
            return self._schedules[park]
        return self._schedules.get("*")

    def for_date(self, day: date, park: str | None = None) -> DayHours | None:
        schedule = self._schedule(park)
        return schedule.for_date(day) if schedule is not None else None

    def _window_for(self, day: date, schedule: _Schedule) -> tuple[datetime | None, datetime | None]:
        hours = schedule.for_date(day)
        if not hours:
            return None, None
        open_dt = datetime.combine(day, hours.open, tzinfo=self.tz)
        close_dt = datetime.combine(day, hours.close, tzinfo=self.tz)
        if close_dt <= open_dt:  # fermeture après minuit
            close_dt += timedelta(days=1)
        return open_dt, close_dt

    def window(
        self, day: date, park: str | None = None
    ) -> tuple[datetime | None, datetime | None]:
        schedule = self._schedule(park)
        if schedule is not None:
            return self._window_for(day, schedule)
        # ``park`` absent (et pas de planning générique) → union des parcs ouverts.
        windows: list[tuple[datetime, datetime]] = []
        for sched in self._schedules.values():
            open_dt, close_dt = self._window_for(day, sched)
            if open_dt is not None and close_dt is not None:
                windows.append((open_dt, close_dt))
        if not windows:
            return None, None
        return min(o for o, _ in windows), max(c for _, c in windows)

    # --------------------------------------------------------------- temps --- #

    def local(self, now: datetime) -> datetime:
        return now.astimezone(self.tz)

    def minutes_to_close(self, now: datetime, park: str | None = None) -> float | None:
        _, close_dt = self.window(self.local(now).date(), park)
        if close_dt is None:
            return None
        return (close_dt - self.local(now)).total_seconds() / 60

    def phase(self, now: datetime, park: str | None = None) -> Phase:
        local = self.local(now)
        open_dt, close_dt = self.window(local.date(), park)
        if open_dt is None or close_dt is None:
            return "unknown"
        if local < open_dt:
            return "before_open"
        if local >= close_dt:
            return "closed"

        minutes_since_open = (local - open_dt).total_seconds() / 60
        minutes_to_close = (close_dt - local).total_seconds() / 60

        if minutes_to_close <= EDGE_WINDOW_MINUTES:
            return "closing"
        if minutes_since_open <= EDGE_WINDOW_MINUTES:
            return "opening"
        if PEAK_START_HOUR <= local.hour < PEAK_END_HOUR:
            return "peak"
        return "afternoon"

    def is_open_now(self, now: datetime, park: str | None = None) -> bool:
        return self.phase(now, park) in ("opening", "peak", "afternoon", "closing")

    def next_opening(self, now: datetime) -> datetime | None:
        """Prochaine ouverture (aujourd'hui ou dans les jours suivants), toutes parcs confondus."""
        local = self.local(now)
        for offset in range(0, 8):
            day = local.date() + timedelta(days=offset)
            open_dt, _ = self.window(day)
            if open_dt and open_dt > local:
                return open_dt
        return None
