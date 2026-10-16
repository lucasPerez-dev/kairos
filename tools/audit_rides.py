#!/usr/bin/env python3
"""Audit des attractions à partir de l'historique collecté (lecture seule).

Transforme les snapshots en recommandations concrètes : attractions fantômes à
masquer, priorités à revoir, seuils d'alerte à ajuster.

Usage :
    python tools/audit_rides.py                       # data/kairos.db
    python tools/audit_rides.py --db data/kairos.db --since 7
"""

from __future__ import annotations

import argparse
import json
import sqlite3
import sys
from collections import defaultdict
from datetime import datetime, timedelta, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from src.config import get_settings  # noqa: E402


def _percentile(values: list[int], pct: float) -> float:
    if not values:
        return 0.0
    ordered = sorted(values)
    k = (len(ordered) - 1) * pct
    lo, hi = int(k), min(int(k) + 1, len(ordered) - 1)
    return ordered[lo] + (ordered[hi] - ordered[lo]) * (k - lo)


def main() -> int:
    settings = get_settings()
    parser = argparse.ArgumentParser(description="Audit des attractions (lecture seule).")
    parser.add_argument("--db", default=str(settings.resolve(settings.db_path)))
    parser.add_argument("--since", type=int, default=0, help="Jours à considérer (0 = tout).")
    args = parser.parse_args()

    db = Path(args.db)
    if not db.exists():
        print(f"❌ Base introuvable : {db}")
        return 1

    con = sqlite3.connect(f"file:{db}?mode=ro", uri=True)
    con.row_factory = sqlite3.Row

    rides = {r["id"]: dict(r) for r in con.execute("SELECT * FROM rides")}

    # Seuil d'alerte réellement en vigueur (état persisté), sinon défaut de config.
    cur_threshold = settings.alert_default_threshold_minutes
    row = con.execute("SELECT value FROM state WHERE key = 'alert_threshold_minutes'").fetchone()
    if row:
        try:
            cur_threshold = int(json.loads(row["value"]))
        except (TypeError, ValueError):
            pass
    if args.since > 0:
        cutoff = (datetime.now(timezone.utc) - timedelta(days=args.since)).isoformat()
        where, params = "WHERE ts >= ?", (cutoff,)
    else:
        where, params = "", ()

    stats: dict[int, dict] = defaultdict(lambda: {"open": [], "total": 0, "zero": 0})
    for row in con.execute(f"SELECT ride_id, wait, is_open FROM snapshots {where}", params):
        st = stats[row["ride_id"]]
        st["total"] += 1
        if row["is_open"]:
            st["open"].append(row["wait"])
            if row["wait"] == 0:
                st["zero"] += 1

    alerts = defaultdict(int)
    for row in con.execute("SELECT ride_id, COUNT(*) c FROM alerts GROUP BY ride_id"):
        alerts[row["ride_id"]] = row["c"]

    rng = con.execute("SELECT MIN(ts), MAX(ts) FROM snapshots").fetchone()
    print(f"=== Audit Kairos — {db} ===")
    print(f"Période : {rng[0]} → {rng[1]}\n")

    ghosts, under, over, noisy, thresholds = [], [], [], [], []
    for rid, st in stats.items():
        ride = rides.get(rid)
        if not ride or st["total"] == 0:
            continue
        if not st["open"]:
            ghosts.append(ride)
            continue
        avg = sum(st["open"]) / len(st["open"])
        p25 = _percentile(st["open"], 0.25)
        mx = max(st["open"])
        prio = ride["priority"]
        if avg >= 30 and prio < 3:
            under.append((ride, avg, mx, prio))
        if avg <= 5 and prio == 3:
            over.append((ride, avg, prio))
        # Attraction quasi toujours vide mais encore alertable → bruit (les réouvertures
        # alertent même si le seuil n'est jamais atteint).
        if avg <= 5 and prio == 2 and alerts.get(rid, 0) > 0:
            noisy.append((ride, avg, alerts[rid]))
        # Seuils uniquement pour les attractions réellement chargées : on alerte sur
        # une attente inhabituellement basse pour elles (percentile 25).
        if avg >= 12:
            suggested = int(max(5, min(45, round(p25))))
            if abs(suggested - cur_threshold) >= 5:
                thresholds.append((ride, avg, mx, suggested))

    ghosts.sort(key=lambda r: r["name"])
    print(f"-- Attractions fantômes (jamais ouvertes sur la période) : {len(ghosts)}")
    print("   À masquer pour nettoyer /liste et le dashboard :")
    for ride in ghosts:
        print(f"   /masquer {ride['name']}")
    print()

    print(f"-- Attractions sous-priorisées (moy ≥ 30 min mais priorité < 3) : {len(under)}")
    for ride, avg, mx, prio in sorted(under, key=lambda x: -x[1]):
        print(f"   {ride['name']:<28} moy {avg:.0f} · max {mx} · prio {prio} → /prio {ride['name']} 3")
    print()

    print(f"-- Attractions sur-priorisées (priorité 3 mais moy ≤ 5 min) : {len(over)}")
    for ride, avg, prio in sorted(over, key=lambda x: x[1]):
        print(f"   {ride['name']:<28} moy {avg:.0f} · prio {prio} → /prio {ride['name']} 2")
    print()

    print(f"-- Bruit d'alertes (quasi toujours vides mais encore alertables) : {len(noisy)}")
    for ride, avg, count in sorted(noisy, key=lambda x: -x[2]):
        print(f"   {ride['name']:<28} moy {avg:.0f} · {count} alerte(s) → /prio {ride['name']} 1 pour la rendre muette")
    print()

    print("-- Seuils d'alerte personnalisés (attractions chargées) :")
    for ride, avg, mx, suggested in sorted(thresholds, key=lambda x: x[1]):
        print(f"   {ride['name']:<28} moy {avg:.0f} · max {mx} → /seuil {ride['name']} {suggested}")
    print()

    print("-- Attractions les plus alertées :")
    for rid, count in sorted(alerts.items(), key=lambda x: -x[1])[:8]:
        name = rides.get(rid, {}).get("name", rid)
        print(f"   {count:>3}  {name}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
