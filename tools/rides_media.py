#!/usr/bin/env python3
"""Médias des attractions : rapport des manquants et récupération d'images.

Usage :
    python tools/rides_media.py report
    python tools/rides_media.py fetch [--only 593,600] [--limit 10] [--force] [--dry-run]

`fetch` cherche une photo libre sur Wikimedia Commons (usage personnel), l'écrit
dans `static/rides/<id>.<ext>` et enregistre le crédit dans `config/rides_credits.json`.
"""

from __future__ import annotations

import argparse
import json
import re
import sys
import time
from pathlib import Path

import httpx

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from src.config import get_settings  # noqa: E402

IMAGE_EXTS = (".jpg", ".jpeg", ".png", ".webp")
COMMONS_API = "https://commons.wikimedia.org/w/api.php"
USER_AGENT = "Kairos/1.0 (projet personnel; contact local)"
TAG_RE = re.compile(r"<[^>]+>")


def _load_rides(path: Path) -> list[dict]:
    data = json.loads(path.read_text(encoding="utf-8"))
    return data.get("rides", [])


def _load_map(path: Path) -> dict:
    if not path.exists():
        return {}
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except json.JSONDecodeError:
        return {}
    return {k: v for k, v in data.items() if not str(k).startswith("_")}


def _image_file(images_dir: Path, ride_id: int) -> Path | None:
    for ext in IMAGE_EXTS:
        candidate = images_dir / f"{ride_id}{ext}"
        if candidate.exists():
            return candidate
    return None


def _strip_html(text: str) -> str:
    return TAG_RE.sub("", text or "").strip()


def _norm(text: str) -> str:
    return re.sub(r"[^a-z0-9]+", " ", (text or "").lower()).strip()


def cmd_report(settings) -> int:
    rides = _load_rides(settings.resolve(settings.rides_config))
    descs = _load_map(settings.resolve(settings.descriptions_config))
    credits = _load_map(settings.resolve(settings.credits_config))
    images_dir = settings.resolve(settings.rides_images_dir)

    no_img = [r for r in rides if _image_file(images_dir, r["id"]) is None]
    no_desc = [r for r in rides if not descs.get(str(r["id"]))]
    no_credit = [
        r for r in rides
        if _image_file(images_dir, r["id"]) is not None and not credits.get(str(r["id"]))
    ]

    print(f"Images présentes  : {len(rides) - len(no_img)}/{len(rides)}")
    print(f"Descriptions      : {len(rides) - len(no_desc)}/{len(rides)}")
    print(f"\n-- Sans image ({len(no_img)}) — à déposer en static/rides/<id>.<ext> :")
    for ride in no_img:
        print(f"   {ride['id']:>6}  {ride['name']}  ({ride['park']})")
    print(f"\n-- Sans description ({len(no_desc)}) — à compléter dans config/rides_descriptions.json :")
    for ride in no_desc:
        print(f"   {ride['id']:>6}  {ride['name']}")
    if no_credit:
        print(f"\n-- Image sans crédit ({len(no_credit)}) — config/rides_credits.json :")
        for ride in no_credit:
            print(f"   {ride['id']:>6}  {ride['name']}")
    return 0


def _commons_image(client: httpx.Client, name: str) -> dict | None:
    """Première image exploitable (JPEG/PNG) trouvée sur Commons pour ce nom."""
    params = {
        "action": "query", "format": "json", "generator": "search",
        "gsrsearch": f"{name} PortAventura", "gsrnamespace": "6", "gsrlimit": "8",
        "prop": "imageinfo", "iiprop": "url|extmetadata|mime", "iiurlwidth": "1200",
    }
    resp = client.get(COMMONS_API, params=params)
    resp.raise_for_status()
    pages = (resp.json().get("query") or {}).get("pages") or {}

    # On exige qu'un mot significatif du nom apparaisse dans le titre du fichier,
    # sinon on risquerait de récupérer la photo d'une autre attraction.
    needle = _norm(name)
    tokens = [t for t in needle.split() if len(t) >= 3]

    best: tuple[tuple[int, int], dict, dict] | None = None
    for page in pages.values():
        info = (page.get("imageinfo") or [{}])[0]
        mime = info.get("mime", "")
        if mime not in ("image/jpeg", "image/png") or not info.get("thumburl"):
            continue
        title = _norm(page.get("title", ""))
        matches = 2 if (needle and needle in title) else sum(1 for t in tokens if t in title)
        if matches == 0:
            continue
        rank = (-matches, 0 if mime == "image/jpeg" else 1)  # plus de mots = mieux ; JPEG préféré
        if best is None or rank < best[0]:
            best = (rank, page, info)
    if best is None:
        return None

    _, page, info = best
    meta = info.get("extmetadata") or {}
    return {
        "url": info.get("thumburl") or info.get("url"),
        "mime": info.get("mime"),
        "author": _strip_html((meta.get("Artist") or {}).get("value", "")),
        "license": (meta.get("LicenseShortName") or {}).get("value", ""),
        "source": info.get("descriptionurl") or "",
    }


def cmd_fetch(settings, args) -> int:
    rides = _load_rides(settings.resolve(settings.rides_config))
    images_dir = settings.resolve(settings.rides_images_dir)
    images_dir.mkdir(parents=True, exist_ok=True)
    credits_path = settings.resolve(settings.credits_config)
    credits = _load_map(credits_path)

    only = {int(x) for x in args.only.split(",")} if args.only else None
    targets = [
        r for r in rides
        if (only is None or r["id"] in only)
        and (args.force or _image_file(images_dir, r["id"]) is None)
    ]
    if args.limit:
        targets = targets[: args.limit]

    if not targets:
        print("Rien à récupérer.")
        return 0

    print(f"{len(targets)} attraction(s) à traiter"
          + (" (simulation)" if args.dry_run else "") + ".")
    done = 0
    headers = {"User-Agent": USER_AGENT}
    with httpx.Client(headers=headers, timeout=30.0, follow_redirects=True) as client:
        for ride in targets:
            name = ride["name"]
            try:
                found = _commons_image(client, name)
            except Exception as exc:  # noqa: BLE001
                print(f"  ✗ {name} : erreur API ({exc})")
                continue
            if not found:
                print(f"  – {name} : aucune image trouvée")
                continue

            if args.dry_run:
                print(f"  • {name} → {found['url'][:70]}… [{found['license']}]")
                continue

            try:
                img = client.get(found["url"])
                img.raise_for_status()
            except Exception as exc:  # noqa: BLE001
                print(f"  ✗ {name} : téléchargement échoué ({exc})")
                continue

            ext = ".png" if found["mime"] == "image/png" else ".jpg"
            dest = images_dir / f"{ride['id']}{ext}"
            dest.write_bytes(img.content)
            credits[str(ride["id"])] = {
                "author": found["author"],
                "license": found["license"],
                "source": found["source"],
            }
            print(f"  ✓ {name} → {dest.name} ({len(img.content) // 1024} Ko, {found['license']})")
            done += 1
            time.sleep(0.4)

    if not args.dry_run and done:
        credits_path.write_text(
            json.dumps(credits, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
        )
        print(f"Crédits écrits dans {credits_path.relative_to(ROOT)} ({len(credits)} entrées).")
    return 0


def main() -> int:
    settings = get_settings()
    parser = argparse.ArgumentParser(description="Médias des attractions (images + descriptions).")
    sub = parser.add_subparsers(dest="cmd", required=True)
    sub.add_parser("report", help="Liste les manquants (images / descriptions / crédits).")
    fetch = sub.add_parser("fetch", help="Récupère les images manquantes depuis Wikimedia Commons.")
    fetch.add_argument("--only", help="Ids séparés par des virgules (ex. 593,600).")
    fetch.add_argument("--limit", type=int, default=0, help="Limiter le nombre d'essais.")
    fetch.add_argument("--force", action="store_true", help="Écraser les images existantes.")
    fetch.add_argument("--dry-run", action="store_true", help="Ne rien écrire, juste montrer.")
    args = parser.parse_args()

    if args.cmd == "report":
        return cmd_report(settings)
    return cmd_fetch(settings, args)


if __name__ == "__main__":
    raise SystemExit(main())
