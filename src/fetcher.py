"""Client pour l'API publique queue-times.com (temps d'attente en direct)."""

from __future__ import annotations

import asyncio
import logging
from datetime import datetime, timezone

import httpx

from .models import RawRide
from .net import ipv4_transport

logger = logging.getLogger(__name__)

QUEUE_TIMES_URL = "https://queue-times.com/parks/{park_id}/queue_times.json"
USER_AGENT = "Kairos/1.0 (+https://queue-times.com/en-US)"


class QueueTimesFetcher:
    def __init__(self, parks: dict[str, int], timeout: float = 15.0, force_ipv4: bool = True) -> None:
        self.parks = parks
        kwargs: dict = {"timeout": timeout}
        if force_ipv4:
            kwargs["transport"] = ipv4_transport()
        self._client = httpx.AsyncClient(
            headers={"User-Agent": USER_AGENT, "Accept": "application/json"},
            **kwargs,
        )

    async def aclose(self) -> None:
        await self._client.aclose()

    async def fetch_park(self, park_id: int) -> list[RawRide]:
        url = QUEUE_TIMES_URL.format(park_id=park_id)
        resp = await self._client.get(url)
        resp.raise_for_status()
        payload = resp.json()

        raw_rides: list[dict] = list(payload.get("rides") or [])
        for land in payload.get("lands") or []:
            raw_rides.extend(land.get("rides") or [])

        results: list[RawRide] = []
        for ride in raw_rides:
            results.append(
                RawRide(
                    ride_id=int(ride["id"]),
                    is_open=bool(ride.get("is_open")),
                    wait_minutes=int(ride.get("wait_time") or 0),
                    updated=_parse_ts(ride.get("last_updated")),
                )
            )
        return results

    async def fetch_all(self) -> dict[str, list[RawRide]]:
        """Renvoie {park_key: [RawRide]} en tolérant les pannes par parc."""
        keys = list(self.parks.keys())
        results = await asyncio.gather(
            *(self.fetch_park(self.parks[key]) for key in keys),
            return_exceptions=True,
        )
        output: dict[str, list[RawRide]] = {}
        for key, result in zip(keys, results):
            if isinstance(result, BaseException):
                logger.warning("Échec du fetch pour %s : %s", key, result)
                output[key] = []
            else:
                output[key] = result
        return output


def _parse_ts(value: str | None) -> datetime | None:
    if not value:
        return None
    try:
        return datetime.fromisoformat(value.replace("Z", "+00:00")).astimezone(timezone.utc)
    except ValueError:
        return None
