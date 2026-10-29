"""Persistance SQLite (SQLite en mode WAL)."""

from __future__ import annotations

import json
import sqlite3
import threading
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Iterable

from .models import Ride

SCHEMA = """
CREATE TABLE IF NOT EXISTS rides (
    id       INTEGER PRIMARY KEY,
    name     TEXT    NOT NULL,
    park     TEXT    NOT NULL,
    zone     TEXT    NOT NULL DEFAULT '',
    category TEXT    NOT NULL DEFAULT 'family',
    priority INTEGER NOT NULL DEFAULT 2
);

CREATE TABLE IF NOT EXISTS snapshots (
    id      INTEGER PRIMARY KEY AUTOINCREMENT,
    ride_id INTEGER NOT NULL,
    ts      TEXT    NOT NULL,
    wait    INTEGER NOT NULL,
    is_open INTEGER NOT NULL
);
CREATE UNIQUE INDEX IF NOT EXISTS idx_snapshots_ride_ts ON snapshots(ride_id, ts);
CREATE INDEX IF NOT EXISTS idx_snapshots_ts ON snapshots(ts);

CREATE TABLE IF NOT EXISTS state (
    key        TEXT PRIMARY KEY,
    value      TEXT NOT NULL,
    updated_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS done (
    ride_id INTEGER PRIMARY KEY,
    ts      TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS alerts (
    id      INTEGER PRIMARY KEY AUTOINCREMENT,
    ride_id INTEGER NOT NULL,
    kind    TEXT    NOT NULL,
    ts      TEXT    NOT NULL,
    value   INTEGER
);
CREATE INDEX IF NOT EXISTS idx_alerts_ride_kind_ts ON alerts(ride_id, kind, ts);
"""


def utcnow() -> datetime:
    return datetime.now(timezone.utc)


def iso(dt: datetime) -> str:
    return dt.astimezone(timezone.utc).isoformat()


def parse_iso(text: str) -> datetime:
    return datetime.fromisoformat(text)


class Store:
    def __init__(self, db_path: str | Path) -> None:
        self.path = Path(db_path)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self._lock = threading.RLock()
        self._conn = sqlite3.connect(self.path, check_same_thread=False)
        self._conn.row_factory = sqlite3.Row
        with self._lock:
            self._conn.execute("PRAGMA journal_mode=WAL")
            self._conn.execute("PRAGMA synchronous=NORMAL")
            self._conn.executescript(SCHEMA)
            self._conn.commit()
        self._migrate()

    def _migrate(self) -> None:
        """Garantit l'unicité (ride_id, ts) et nettoie les doublons hérités."""
        with self._lock:
            indexes = {
                row["name"]: bool(row["unique"])
                for row in self._conn.execute("PRAGMA index_list(snapshots)")
            }
            if indexes.get("idx_snapshots_ride_ts"):
                return  # déjà unique
            self._conn.execute(
                "DELETE FROM snapshots WHERE id NOT IN "
                "(SELECT MIN(id) FROM snapshots GROUP BY ride_id, ts)"
            )
            self._conn.execute("DROP INDEX IF EXISTS idx_snapshots_ride_ts")
            self._conn.execute(
                "CREATE UNIQUE INDEX idx_snapshots_ride_ts ON snapshots(ride_id, ts)"
            )
            self._conn.commit()

    def close(self) -> None:
        with self._lock:
            self._conn.close()

    # ----------------------------------------------------------- rides --- #

    def sync_rides(self, rides: Iterable[Ride]) -> None:
        """Insère les attractions manquantes sans écraser priorités/zones existantes."""
        with self._lock:
            self._conn.executemany(
                """
                INSERT INTO rides (id, name, park, zone, category, priority)
                VALUES (?, ?, ?, ?, ?, ?)
                ON CONFLICT(id) DO UPDATE SET
                    name = excluded.name,
                    park = excluded.park
                """,
                [(r.id, r.name, r.park, r.zone, r.category, r.priority) for r in rides],
            )
            self._conn.commit()

    def hidden_ride_ids(self) -> set[int]:
        """Attractions masquées (jamais proposées) : utile pour les attractions fantômes."""
        raw = self.get_state("hidden_rides") or []
        try:
            return {int(x) for x in raw}
        except (TypeError, ValueError):
            return set()

    def set_ride_hidden(self, ride_id: int, hidden: bool) -> None:
        ids = self.hidden_ride_ids()
        if hidden:
            ids.add(ride_id)
        else:
            ids.discard(ride_id)
        self.set_state("hidden_rides", sorted(ids))

    def rides(self, include_hidden: bool = False) -> list[Ride]:
        """Attractions. Par défaut, les attractions masquées sont exclues.

        ``include_hidden=True`` sert à la collecte et aux lookups (résolution par nom),
        pour ne pas perdre l'historique ni empêcher de les réafficher.
        """
        with self._lock:
            rows = self._conn.execute(
                "SELECT id, name, park, zone, category, priority FROM rides ORDER BY park, name"
            ).fetchall()
        hidden = set() if include_hidden else self.hidden_ride_ids()
        return [
            Ride(
                id=row["id"],
                name=row["name"],
                park=row["park"],
                zone=row["zone"],
                category=row["category"],
                priority=row["priority"],
            )
            for row in rows
            if row["id"] not in hidden
        ]

    def set_priority(self, ride_id: int, priority: int) -> None:
        with self._lock:
            self._conn.execute(
                "UPDATE rides SET priority = ? WHERE id = ?", (priority, ride_id)
            )
            self._conn.commit()

    # ------------------------------------------------------- snapshots --- #

    def add_snapshots(self, rows: Iterable[tuple[int, datetime, int, bool]]) -> None:
        with self._lock:
            self._conn.executemany(
                "INSERT OR IGNORE INTO snapshots (ride_id, ts, wait, is_open) VALUES (?, ?, ?, ?)",
                [(rid, iso(ts), wait, 1 if is_open else 0) for rid, ts, wait, is_open in rows],
            )
            self._conn.commit()

    def latest(self) -> dict[int, dict[str, Any]]:
        """Dernier état connu par attraction."""
        with self._lock:
            rows = self._conn.execute(
                """
                SELECT s.ride_id, s.ts, s.wait, s.is_open
                FROM snapshots s
                JOIN (SELECT ride_id, MAX(id) AS max_id FROM snapshots GROUP BY ride_id) m
                  ON m.max_id = s.id
                """
            ).fetchall()
        return {
            row["ride_id"]: {
                "ts": parse_iso(row["ts"]),
                "wait": row["wait"],
                "is_open": bool(row["is_open"]),
            }
            for row in rows
        }

    def history(self, ride_id: int, minutes: int = 45, now: datetime | None = None) -> list[tuple[datetime, int, bool]]:
        now = now or utcnow()
        since = iso(now - timedelta(minutes=minutes))
        with self._lock:
            rows = self._conn.execute(
                """
                SELECT ts, wait, is_open FROM snapshots
                WHERE ride_id = ? AND ts >= ? AND is_open = 1
                ORDER BY ts ASC
                """,
                (ride_id, since),
            ).fetchall()
        return [(parse_iso(r["ts"]), r["wait"], bool(r["is_open"])) for r in rows]

    def open_waits_by_ride(
        self, since: datetime | None = None
    ) -> dict[int, list[tuple[datetime, int]]]:
        """Attentes observées (ouvertes) groupées par attraction — pour la baseline des alertes."""
        with self._lock:
            if since is not None:
                rows = self._conn.execute(
                    "SELECT ride_id, ts, wait FROM snapshots WHERE is_open = 1 AND ts >= ? ORDER BY ride_id, ts",
                    (iso(since),),
                ).fetchall()
            else:
                rows = self._conn.execute(
                    "SELECT ride_id, ts, wait FROM snapshots WHERE is_open = 1 ORDER BY ride_id, ts"
                ).fetchall()
        out: dict[int, list[tuple[datetime, int]]] = {}
        for row in rows:
            out.setdefault(row["ride_id"], []).append((parse_iso(row["ts"]), row["wait"]))
        return out

    def ride_open_waits(
        self, ride_id: int, since: datetime | None = None
    ) -> list[tuple[datetime, int]]:
        """Toutes les attentes observées (attraction ouverte), pour le profil horaire."""
        with self._lock:
            if since is not None:
                rows = self._conn.execute(
                    "SELECT ts, wait FROM snapshots WHERE ride_id = ? AND is_open = 1 AND ts >= ? ORDER BY ts",
                    (ride_id, iso(since)),
                ).fetchall()
            else:
                rows = self._conn.execute(
                    "SELECT ts, wait FROM snapshots WHERE ride_id = ? AND is_open = 1 ORDER BY ts",
                    (ride_id,),
                ).fetchall()
        return [(parse_iso(r["ts"]), r["wait"]) for r in rows]

    def prune_snapshots(self, before: datetime) -> int:
        with self._lock:
            cur = self._conn.execute("DELETE FROM snapshots WHERE ts < ?", (iso(before),))
            self._conn.commit()
            return cur.rowcount

    # ------------------------------------------------------------ done --- #

    def mark_done(self, ride_id: int) -> None:
        with self._lock:
            self._conn.execute(
                "INSERT INTO done (ride_id, ts) VALUES (?, ?) ON CONFLICT(ride_id) DO UPDATE SET ts = excluded.ts",
                (ride_id, iso(utcnow())),
            )
            self._conn.commit()

    def unmark_done(self, ride_id: int) -> None:
        with self._lock:
            self._conn.execute("DELETE FROM done WHERE ride_id = ?", (ride_id,))
            self._conn.commit()

    def done_ids(self) -> set[int]:
        with self._lock:
            rows = self._conn.execute("SELECT ride_id FROM done").fetchall()
        return {row["ride_id"] for row in rows}

    # ----------------------------------------------------------- state --- #

    def get_state(self, key: str, default: Any = None) -> Any:
        with self._lock:
            row = self._conn.execute("SELECT value FROM state WHERE key = ?", (key,)).fetchone()
        if row is None:
            return default
        try:
            return json.loads(row["value"])
        except json.JSONDecodeError:
            return default

    def set_state(self, key: str, value: Any) -> None:
        with self._lock:
            self._conn.execute(
                """
                INSERT INTO state (key, value, updated_at) VALUES (?, ?, ?)
                ON CONFLICT(key) DO UPDATE SET value = excluded.value, updated_at = excluded.updated_at
                """,
                (key, json.dumps(value, ensure_ascii=False), iso(utcnow())),
            )
            self._conn.commit()

    # ---------------------------------------------------------- alerts --- #

    def log_alert(self, ride_id: int, kind: str, value: int | None = None) -> None:
        with self._lock:
            self._conn.execute(
                "INSERT INTO alerts (ride_id, kind, ts, value) VALUES (?, ?, ?, ?)",
                (ride_id, kind, iso(utcnow()), value),
            )
            self._conn.commit()

    def last_alert_ts(self, ride_id: int, kind: str | None = None) -> datetime | None:
        """Dernière alerte pour une attraction. ``kind=None`` = toutes catégories confondues."""
        with self._lock:
            if kind is None:
                row = self._conn.execute(
                    "SELECT ts FROM alerts WHERE ride_id = ? ORDER BY id DESC LIMIT 1",
                    (ride_id,),
                ).fetchone()
            else:
                row = self._conn.execute(
                    "SELECT ts FROM alerts WHERE ride_id = ? AND kind = ? ORDER BY id DESC LIMIT 1",
                    (ride_id, kind),
                ).fetchone()
        return parse_iso(row["ts"]) if row else None

    # ----------------------------------------------------------- stats --- #

    def stats(self) -> dict[str, Any]:
        with self._lock:
            row = self._conn.execute(
                "SELECT COUNT(*) AS n, MIN(ts) AS first_ts, MAX(ts) AS last_ts FROM snapshots"
            ).fetchone()
        return {
            "snapshots": row["n"] or 0,
            "first": parse_iso(row["first_ts"]) if row["first_ts"] else None,
            "last": parse_iso(row["last_ts"]) if row["last_ts"] else None,
        }
