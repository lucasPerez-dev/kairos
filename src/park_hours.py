"""Horaires d'ouverture et phases de la journée."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date, datetime, time, timedelta
from zoneinfo import ZoneInfo

Phase = str  # before_open | opening | peak | afternoon | closing | closed | unknown

PEAK_START_HOUR = 11
PEAK_END_HOUR = 15
EDGE_WINDOW_MINUTES = 90  # fenêtre "ouverture" / "fermeture"


@dataclass(frozen=True)
class DayHours:
    open: time
    close: time
    estimated: bool = False


class ParkHours:
    def __init__(self, config: dict, timezone: str) -> None:
        self.tz = ZoneInfo(timezone)
        self.default = self._parse(config.get("default")) if config.get("default") else None
        self.days: dict[str, DayHours] = {
            key: parsed
            for key, value in (config.get("days") or {}).items()
            if (parsed := self._parse(value))
        }

    @staticmethod
    def _parse(value: dict | None) -> DayHours | None:
        if not value:
            return None
        try:
            return DayHours(
                open=time.fromisoformat(value["open"]),
                close=time.fromisoformat(value["close"]),
                estimated=bool(value.get("estimated", False)),
            )
        except (KeyError, ValueError):
            return None

    def for_date(self, day: date) -> DayHours | None:
        return self.days.get(day.isoformat(), self.default)

    def window(self, day: date) -> tuple[datetime | None, datetime | None]:
        hours = self.for_date(day)
        if not hours:
            return None, None
        return (
            datetime.combine(day, hours.open, tzinfo=self.tz),
            datetime.combine(day, hours.close, tzinfo=self.tz),
        )

    def local(self, now: datetime) -> datetime:
        return now.astimezone(self.tz)

    def minutes_to_close(self, now: datetime) -> float | None:
        _, close_dt = self.window(self.local(now).date())
        if close_dt is None:
            return None
        return (close_dt - self.local(now)).total_seconds() / 60

    def phase(self, now: datetime) -> Phase:
        local = self.local(now)
        open_dt, close_dt = self.window(local.date())
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

    def is_open_now(self, now: datetime) -> bool:
        return self.phase(now) in ("opening", "peak", "afternoon", "closing")

    def next_opening(self, now: datetime) -> datetime | None:
        """Prochaine ouverture (aujourd'hui ou dans les jours suivants)."""
        local = self.local(now)
        for offset in range(0, 8):
            day = local.date() + timedelta(days=offset)
            open_dt, close_dt = self.window(day)
            if open_dt and close_dt:
                candidate = open_dt if offset > 0 else open_dt
                if candidate > local:
                    return candidate
        return None
