#!/usr/bin/env python3
"""Régénère config/rides.json depuis queue-times.com.

Conserve `zone`, `category` et `priority` des attractions déjà connues (par id),
signale les nouvelles attractions et celles qui ont disparu.

Usage :  python tools/refresh_rides.py
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

import httpx

ROOT = Path(__file__).resolve().parent.parent
RIDES_PATH = ROOT / "config" / "rides.json"
QUEUE_TIMES_URL = "https://queue-times.com/parks/{park_id}/queue_times.json"


def main() -> int:
    config = json.loads(RIDES_PATH.read_text(encoding="utf-8"))
    parks = config["parks"]
    by_id = {int(item["id"]): item for item in config["rides"]}

    with httpx.Client(timeout=20, headers={"User-Agent": "Kairos/1.0"}) as client:
        for park in parks:
            resp = client.get(QUEUE_TIMES_URL.format(park_id=park["queue_times_id"]))
            resp.raise_for_status()
            payload = resp.json()
            rides = list(payload.get("rides") or [])
            for land in payload.get("lands") or []:
                rides.extend(land.get("rides") or [])
            for ride in rides:
                ride_id = int(ride["id"])
                if ride_id in by_id:
                    by_id[ride_id]["name"] = ride["name"]
                else:
                    print(f"+ nouvelle attraction : {ride['name']} (id {ride_id}) dans {park['name']}")
                    by_id[ride_id] = {
                        "id": ride_id,
                        "name": ride["name"],
                        "park": park["key"],
                        "zone": "?",
                        "category": "family",
                        "priority": 2,
                    }

    config["rides"] = sorted(by_id.values(), key=lambda r: (r["park"], r["name"]))
    RIDES_PATH.write_text(json.dumps(config, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(f"✓ {len(config['rides'])} attractions écrites dans {RIDES_PATH}")
    print("⚠️  Vérifie les nouvelles attractions marquées zone '?'")
    return 0


if __name__ == "__main__":
    sys.exit(main())
