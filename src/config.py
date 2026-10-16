"""Configuration de Kairos (variables d'environnement + fichiers JSON)."""

from __future__ import annotations

import json
from functools import lru_cache
from pathlib import Path
from typing import Any, Literal

from pydantic_settings import BaseSettings, SettingsConfigDict

BASE_DIR = Path(__file__).resolve().parent.parent


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=str(BASE_DIR / ".env"),
        env_prefix="KAIROS_",
        extra="ignore",
    )

    # Telegram
    telegram_bot_token: str = ""
    telegram_chat_id: str = ""

    # Fonctionnement
    mode: Literal["collect", "trip"] = "collect"
    poll_interval_seconds: int = 300
    alert_default_threshold_minutes: int = 15
    alert_cooldown_minutes: int = 30

    # Dashboard
    dashboard_enabled: bool = True
    dashboard_host: str = "0.0.0.0"
    dashboard_port: int = 8080

    # Divers
    timezone: str = "Europe/Madrid"
    force_ipv4: bool = True
    skip_when_closed: bool = True
    db_path: str = "data/kairos.db"
    history_retention_days: int = 0

    # Fichiers de config
    rides_config: str = "config/rides.json"
    hours_config: str = "config/hours.json"
    zones_config: str = "config/zones.json"

    # Ressources des attractions (fiche : image + description + crédit)
    rides_images_dir: str = "static/rides"
    descriptions_config: str = "config/rides_descriptions.json"
    credits_config: str = "config/rides_credits.json"

    # Attribution (obligatoire vis-à-vis de queue-times.com)
    attribution_text: str = "Powered by Queue-Times.com"
    attribution_url: str = "https://queue-times.com/en-US"

    # ------------------------------------------------------------------ #

    @property
    def bot_enabled(self) -> bool:
        return bool(self.telegram_bot_token.strip())

    @property
    def chat_id(self) -> int | None:
        raw = self.telegram_chat_id.strip()
        if not raw:
            return None
        try:
            return int(raw)
        except ValueError:
            return None

    @property
    def is_trip_mode(self) -> bool:
        return self.mode == "trip"

    def resolve(self, path: str | Path) -> Path:
        p = Path(path)
        return p if p.is_absolute() else BASE_DIR / p


@lru_cache
def get_settings() -> Settings:
    return Settings()


def load_json(path: str | Path) -> dict[str, Any]:
    with open(path, "r", encoding="utf-8") as fh:
        return json.load(fh)
