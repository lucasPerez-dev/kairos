#!/usr/bin/env python3
"""Rejoue l'historique pour comparer l'alerting fixe et l'alerting adaptatif.

Usage :
    python tools/backtest_alerts.py                     # 5 derniers jours, agressif
    python tools/backtest_alerts.py --days 3 --sensitivity prudent --coasters-only
    python tools/backtest_alerts.py --detail            # détail du dernier jour

La baseline de chaque jour est construite uniquement avec les données **antérieures**
à ce jour (pas de fuite du futur). Lecture seule.
"""

from __future__ import annotations

import argparse
import json
import sqlite3
import sys
from collections import defaultdict
from datetime import date, datetime, timedelta, timezone
from pathlib import Path
from statistics import median
from zoneinfo import ZoneInfo

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from src.alerts import SENSITIVITIES, adaptive_cut  # noqa: E402
from src.config import get_settings  # noqa: E402


def _parse(value: str) -> datetime:
    return datetime.fromisoformat(value).astimezone(timezone.utc)


def main() -> int:
    settings = get_settings()
    parser = argparse.ArgumentParser(description="Backtest des alertes (lecture seule).")
    parser.add_argument("--db", default=settings.db_path)
    parser.add_argument("--days", type=int, default=5, help="Nombre de jours à évaluer.")
    parser.add_argument("--sensitivity", choices=tuple(SENSITIVITIES), default="agressif")
    parser.add_argument("--baseline-days", type=int, default=settings.alert_baseline_days)
    parser.add_argument("--min-samples", type=int, default=8)
    parser.add_argument("--cooldown", type=int, default=settings.alert_cooldown_minutes)
    parser.add_argument("--fixed", type=int, default=None,
                        help="Seuil fixe de comparaison (défaut : valeur en base, sinon config).")
    parser.add_argument("--coasters-only", action="store_true")
    parser.add_argument("--detail", action="store_true", help="Détail du dernier jour.")
    args = parser.parse_args()

    db = settings.resolve(args.db)
    if not db.exists():
        print(f"❌ Base introuvable : {db}")
        return 1

    con = sqlite3.connect(f"file:{db}?mode=ro", uri=True)
    con.row_factory = sqlite3.Row
    rides = {r["id"]: dict(r) for r in con.execute("SELECT * FROM rides")}
    tz = ZoneInfo(settings.timezone)

    fixed = args.fixed
    if fixed is None:
        row = con.execute("SELECT value FROM state WHERE key = 'alert_threshold_minutes'").fetchone()
        if row:
            try:
                fixed = int(json.loads(row["value"]))
            except (TypeError, ValueError):
                fixed = None
        fixed = settings.alert_default_threshold_minutes if fixed is None else fixed

    seq: dict[int, list[tuple[datetime, int, bool]]] = defaultdict(list)
    for r in con.execute("SELECT ride_id, ts, wait, is_open FROM snapshots ORDER BY ride_id, ts"):
        seq[r["ride_id"]].append((_parse(r["ts"]), r["wait"], bool(r["is_open"])))

    def eligible(ride: dict) -> bool:
        if ride["priority"] < 2:
            return False
        return not args.coasters_only or ride["category"] == "coaster"

    all_days = sorted({ts.astimezone(tz).date() for points in seq.values() for ts, _, _ in points})
    eval_days = all_days[-args.days :]

    def baseline_for(day: date) -> tuple[dict[tuple[int, int], list[int]], dict[int, list[int]]]:
        floor = day - timedelta(days=args.baseline_days)
        by_hour: dict[tuple[int, int], list[int]] = defaultdict(list)
        by_all: dict[int, list[int]] = defaultdict(list)
        for rid, points in seq.items():
            for ts, wait, is_open in points:
                local = ts.astimezone(tz)
                if not is_open or local.date() >= day or local.date() < floor:
                    continue
                by_all[rid].append(wait)
                by_hour[(rid, local.hour)].append(wait)
        return by_hour, by_all

    def simulate(day: date, by_hour, by_all, adaptive: bool) -> list[tuple[datetime, str, int, int | None]]:
        alerts: list[tuple[datetime, str, int, int | None]] = []
        for rid, points in seq.items():
            ride = rides.get(rid)
            if not ride or not eligible(ride):
                continue
            prev_open = False
            prev_wait: int | None = None
            last_alert: datetime | None = None
            for ts, wait, is_open in points:
                local = ts.astimezone(tz)
                if local.date() != day:
                    continue
                if not is_open:
                    prev_open = False
                    continue

                if adaptive:
                    waits = by_hour.get((rid, local.hour)) or by_all.get(rid)
                    if waits and len(waits) >= args.min_samples:
                        cut, typical = adaptive_cut(waits, args.sensitivity)
                    else:
                        cut, typical = float(fixed), None  # démarrage à froid
                else:
                    cut, typical = float(fixed), None

                if (
                    cut is not None
                    and wait < cut
                    and ((prev_wait is None or prev_wait >= cut) or prev_open is False)
                    and (last_alert is None or (ts - last_alert).total_seconds() >= args.cooldown * 60)
                ):
                    alerts.append((local, ride["name"], wait, typical))
                    last_alert = ts
                prev_open = True
                prev_wait = wait
        return alerts

    print(f"Base : {db}")
    print(f"Sensibilité : {args.sensitivity} · cooldown {args.cooldown} min · "
          f"seuil fixe {fixed} · {'coasters uniquement' if args.coasters_only else 'toutes attractions'}")
    print(f"\n{'jour':<10}{'fixe':>8}{'adaptatif':>12}")
    for day in eval_days:
        by_hour, by_all = baseline_for(day)
        fixed_alerts = simulate(day, by_hour, by_all, adaptive=False)
        adaptive_alerts = simulate(day, by_hour, by_all, adaptive=True)
        print(f"{day.strftime('%d/%m/%Y'):<10}{len(fixed_alerts):>8}{len(adaptive_alerts):>12}")

    if args.detail:
        last = eval_days[-1]
        by_hour, by_all = baseline_for(last)
        print(f"\n-- Détail adaptatif du {last.strftime('%d/%m/%Y')} --")
        for local, name, wait, typical in sorted(simulate(last, by_hour, by_all, adaptive=True)):
            context = f"hab. {typical} min" if typical is not None else "seuil de repli"
            print(f"  {local.strftime('%H:%M')}  {name:<28} {wait:>3} min ({context})")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
