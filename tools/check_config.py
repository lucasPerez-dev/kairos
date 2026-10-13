#!/usr/bin/env python3
"""Vérifie la configuration de Kairos (sans jamais afficher les secrets).

Usage :  python tools/check_config.py
"""

from __future__ import annotations

import re
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from src.config import BASE_DIR, get_settings  # noqa: E402

TOKEN_RE = re.compile(r"^\d+:[A-Za-z0-9_-]{30,}$")

OK = "✅"
WARN = "⚠️ "
ERR = "❌"


def main() -> int:
    env_path = BASE_DIR / ".env"
    print(f"Fichier .env attendu : {env_path}")
    if not env_path.exists():
        print(f"{ERR} Introuvable. Copie `.env.example` vers `.env`.")
        return 1

    s = get_settings()
    problems = 0

    token = s.telegram_bot_token.strip()
    if not token:
        print(f"{ERR} KAIROS_TELEGRAM_BOT_TOKEN est vide → le bot ne démarrera pas.")
        print("     Mets le token APRÈS le '=' , sans guillemets ni espace :")
        print("     KAIROS_TELEGRAM_BOT_TOKEN=123456789:AAH...")
        problems += 1
    elif not TOKEN_RE.match(token):
        print(f"{WARN}Le token a une forme inhabituelle (attendu : <chiffres>:<35 car.).")
        problems += 1
    else:
        print(f"{OK} Token présent (…{token[-4:]})")

    if s.chat_id is None:
        print(f"{WARN}Aucun KAIROS_TELEGRAM_CHAT_ID : envoie /id au bot dans le groupe, puis colle l'ID.")
    else:
        print(f"{OK} Chat ID : {s.chat_id}")

    print(f"{OK} Mode : {s.mode}  (collect = pas d'alertes / trip = alertes)")
    print(f"{OK} Poll : toutes les {s.poll_interval_seconds}s · dashboard {s.dashboard_host}:{s.dashboard_port}")

    print()
    print("→" , "Configuration incomplète." if problems else "Configuration prête. Lance : python main.py")
    return 1 if problems else 0


if __name__ == "__main__":
    raise SystemExit(main())
