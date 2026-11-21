"""Deezer-Anbindung: Kartenpool laden und 30-Sekunden-Preview aufloesen.

Die Preview-Links sind mit einem hmac-Token versehen, das nach rund 15 Minuten ablaeuft
(selbst gemessen 21.11.2026: exp 15 Minuten in der Zukunft). Deshalb wird pro Runde frisch
aufgeloest und nichts auf Vorrat gespeichert — laut Deezer-Guidelines ist lokales Speichern
von Audio ohnehin untersagt.
"""

from __future__ import annotations

import json
from pathlib import Path

import httpx

DEEZER = "https://api.deezer.com"
POOL_FILE = Path(__file__).resolve().parent.parent / "data" / "pool.json"
UA = "song-timeline/0.1 (private party game; daniel@familie-heise.de)"


def load_pool() -> list[dict]:
    """Kuratierter Pool: key/title/artist/year/deezer_id. Jahr ist die einzige Wahrheit im Spiel."""
    if not POOL_FILE.exists():
        raise FileNotFoundError(f"Kartenpool fehlt: {POOL_FILE}")
    data = json.loads(POOL_FILE.read_text(encoding="utf-8"))
    cards = data["cards"] if isinstance(data, dict) else data
    return [c for c in cards if c.get("deezer_id")]


async def resolve_preview(deezer_id: str, timeout: float = 8.0) -> str | None:
    """Frische Preview-URL fuer einen Track. None, wenn Deezer keinen Ausschnitt liefert."""
    url = f"{DEEZER}/track/{deezer_id}"
    try:
        async with httpx.AsyncClient(timeout=timeout, headers={"User-Agent": UA}) as client:
            resp = await client.get(url)
            resp.raise_for_status()
            data = resp.json()
    except (httpx.HTTPError, ValueError):
        return None
    preview = data.get("preview")
    if not isinstance(preview, str) or not preview.startswith("http"):
        return None
    return preview


async def search_track(title: str, artist: str, timeout: float = 8.0) -> dict | None:
    """Besten Deezer-Treffer zu Titel+Interpret holen (fuer scripts/build_pool.py)."""
    query = f'{title} {artist}'
    try:
        async with httpx.AsyncClient(timeout=timeout, headers={"User-Agent": UA}) as client:
            resp = await client.get(f"{DEEZER}/search", params={"q": query, "limit": 5})
            resp.raise_for_status()
            data = resp.json()
    except (httpx.HTTPError, ValueError):
        return None
    return (data.get("data") or [None])[0]
