"""Helpers réseau partagés."""

from __future__ import annotations

import httpx


def ipv4_transport(retries: int = 3) -> httpx.AsyncHTTPTransport:
    """Transport forçant l'IPv4.

    Contourne les réseaux où `api.telegram.org` (et d'autres) résolvent en IPv6
    mais où l'IPv6 ne fonctionne pas : sans ça, les connexions traînent puis
    expirent alors que l'IPv4 est parfaitement joignable.
    """
    return httpx.AsyncHTTPTransport(local_address="0.0.0.0", retries=retries)
