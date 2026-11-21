"""Playlist-Import: Deezer-Playlists als Kartenquelle (UI und CLI).

Der Import dauert pro Playlist einige Minuten (pro Track eine Apple-Music-Abfrage fuer das
Jahr), laeuft deshalb als Hintergrund-Job mit Fortschritt statt im HTTP-Request.
"""

from __future__ import annotations

import asyncio
import json
import re
import secrets
import time
import urllib.parse
from dataclasses import dataclass, field
from pathlib import Path

import httpx

from app import catalog
from app.deezer import DEEZER, POOL_FILE, UA

PLAYLISTS_FILE = POOL_FILE.parent / "playlists.json"
PLAYLIST_RE = re.compile(r"playlist/(\d+)")
MIN_SECONDS = 75          # keine Skits/Intros als Karte
YEAR_TOLERANCE = 2        # Abweichung Apple <-> Deezer-Album, ab der eine Karte auffaellt


@dataclass
class Job:
    id: str
    playlist_id: str
    state: str = "queued"          # queued | running | done | error
    title: str = ""
    total: int = 0
    processed: int = 0
    added: int = 0
    message: str = ""
    skipped: dict = field(default_factory=dict)
    started: float = field(default_factory=time.time)
    finished: float | None = None

    def as_dict(self) -> dict:
        return {"id": self.id, "playlist_id": self.playlist_id, "state": self.state,
                "title": self.title, "total": self.total, "processed": self.processed,
                "added": self.added, "message": self.message, "skipped": self.skipped,
                "seconds": round((self.finished or time.time()) - self.started, 1)}


JOBS: dict[str, Job] = {}


# ------------------------------------------------------------------ Dateien

def pool_cards() -> list[dict]:
    if not POOL_FILE.exists():
        return []
    data = json.loads(POOL_FILE.read_text(encoding="utf-8"))
    return data["cards"] if isinstance(data, dict) else data


def load_playlists() -> list[dict]:
    if not PLAYLISTS_FILE.exists():
        return []
    try:
        data = json.loads(PLAYLISTS_FILE.read_text(encoding="utf-8"))
    except ValueError:
        return []
    return data.get("playlists", []) if isinstance(data, dict) else data


def save_playlists(items: list[dict]) -> None:
    PLAYLISTS_FILE.parent.mkdir(parents=True, exist_ok=True)
    PLAYLISTS_FILE.write_text(json.dumps({"version": 1, "playlists": items},
                                         ensure_ascii=False, indent=2), encoding="utf-8")


def parse_playlist_id(raw: str) -> str | None:
    raw = (raw or "").strip()
    if not raw:
        return None
    hit = PLAYLIST_RE.search(raw)
    if hit:
        return hit.group(1)
    if raw.isdigit():
        return raw
    return None


async def resolve_playlist_id(raw: str) -> str | None:
    """ID aus Link, Kurzlink oder roher ID holen.

    Deezer-Kurzlinks (link.deezer.com/s/…) enthalten keine ID; sie leiten auf
    www.deezer.com/playlist/<id> weiter, teils mit der Ziel-URL url-kodiert in Query-Parametern
    (awf/dest/iwf). Deshalb hier einmal folgen und alle Stellen absuchen.
    """
    direct = parse_playlist_id(raw)
    if direct:
        return direct
    raw = (raw or "").strip()
    if not re.match(r"https?://", raw):
        return None
    try:
        async with httpx.AsyncClient(timeout=15.0, follow_redirects=True,
                                     headers={"User-Agent": UA}) as client:
            resp = await client.get(raw)
    except httpx.HTTPError:
        return None
    candidates = [str(resp.url), resp.headers.get("location", "")]
    parsed = urllib.parse.urlparse(str(resp.url))
    for values in urllib.parse.parse_qs(parsed.query).values():
        candidates.extend(urllib.parse.unquote(v) for v in values)
    for candidate in candidates:
        hit = PLAYLIST_RE.search(candidate or "")
        if hit:
            return hit.group(1)
    return None



# ------------------------------------------------------------------ Deezer

async def fetch_playlist(client: httpx.AsyncClient, pid: str) -> tuple[dict, list[dict]]:
    resp = await client.get(f"{DEEZER}/playlist/{pid}")
    resp.raise_for_status()
    meta = resp.json()
    if not meta.get("id"):
        raise ValueError("Playlist nicht lesbar (privat oder geloescht?)")
    tracks: list[dict] = []
    index = 0
    while True:
        page = await client.get(f"{DEEZER}/playlist/{pid}/tracks",
                                params={"limit": 100, "index": index})
        page.raise_for_status()
        data = page.json().get("data") or []
        if not data:
            break
        tracks.extend(data)
        index += len(data)
        if len(tracks) >= (meta.get("nb_tracks") or 0):
            break
        await asyncio.sleep(0.15)
    return meta, tracks


async def album_genres(client: httpx.AsyncClient, album_id) -> list[str]:
    if not album_id:
        return []
    try:
        resp = await client.get(f"{DEEZER}/album/{album_id}")
        resp.raise_for_status()
        album = resp.json()
    except (httpx.HTTPError, ValueError):
        return []
    return [g.get("name") for g in (album.get("genres") or {}).get("data") or [] if g.get("name")]


def same_song(a: dict, b: dict) -> bool:
    if catalog.norm(a.get("artist")) != catalog.norm(b.get("artist")):
        return False
    ta, tb = catalog.norm(a.get("title")), catalog.norm(b.get("title"))
    return bool(ta) and bool(tb) and (ta in tb or tb in ta)


# ------------------------------------------------------------------ Import

async def import_playlist(playlist_id: str, job: Job | None = None, limit: int = 0) -> Job:
    job = job or Job(id=secrets.token_urlsafe(6), playlist_id=playlist_id)
    job.state = "running"
    cards = pool_cards()
    keep = [c for c in cards if c.get("playlist") != playlist_id]     # Re-Import ersetzt
    new_cards: list[dict] = []
    skipped = {"kein_preview": 0, "schlechte_fassung": 0, "zu_kurz": 0, "doppelt": 0,
               "kein_jahr": 0, "schon_vorhanden": 0}

    try:
        async with httpx.AsyncClient(timeout=20.0, headers={"User-Agent": UA}) as client:
            meta, tracks = await fetch_playlist(client, playlist_id)
            job.title = meta.get("title") or playlist_id
            job.total = len(tracks)

            for track in tracks:
                if limit and len(new_cards) >= limit:
                    break
                title = (track.get("title") or "").strip()
                artist = ((track.get("artist") or {}).get("name") or "").strip()
                isrc = (track.get("isrc") or "").strip()
                job.processed += 1
                if not title or not artist:
                    continue
                if not track.get("preview"):
                    skipped["kein_preview"] += 1
                    continue
                if not catalog.is_clean(title):
                    skipped["schlechte_fassung"] += 1
                    continue
                if (track.get("duration") or 0) < MIN_SECONDS:
                    skipped["zu_kurz"] += 1
                    continue
                if any(same_song({"title": title, "artist": artist}, c) for c in keep + new_cards):
                    skipped["schon_vorhanden"] += 1
                    continue
                if isrc and any(c.get("isrc") == isrc for c in keep + new_cards):
                    skipped["doppelt"] += 1
                    continue

                album = track.get("album") or {}
                deezer_year = (album.get("release_date") or "")[:4]
                deezer_year = int(deezer_year) if deezer_year.isdigit() else None
                apple_year = await asyncio.to_thread(catalog.apple_year, title, artist)
                await asyncio.sleep(0.35)

                year = apple_year or deezer_year
                if year is None:
                    skipped["kein_jahr"] += 1
                    continue

                raw_genres = await album_genres(client, album.get("id"))
                genres = catalog.bucket_genres(raw_genres, artist) or [catalog.FALLBACK_GENRE]
                new_cards.append({
                    "key": catalog.slug(title, artist),
                    "title": title,
                    "artist": artist,
                    "year": year,
                    "deezer_id": str(track["id"]),
                    "deezer_release_year": str(deezer_year) if deezer_year else None,
                    "preview_ok": True,
                    "genres": genres,
                    "deezer_genres": raw_genres,
                    "isrc": isrc or None,
                    "year_source": "itunes" if apple_year else "deezer-album",
                    "playlist": playlist_id,
                    "_apple": apple_year,
                    "_deezer": deezer_year,
                })

        review: list[str] = []
        for card in new_cards:
            apple_year, deezer_year = card.pop("_apple"), card.pop("_deezer")
            if apple_year and deezer_year and abs(apple_year - deezer_year) > YEAR_TOLERANCE:
                review.append(f"JAHR ABWEICHEND: {card['title']} — {card['artist']} | "
                              f"Apple {apple_year} | Deezer-Album {deezer_year}")

        merged = keep + new_cards
        POOL_FILE.parent.mkdir(parents=True, exist_ok=True)
        POOL_FILE.write_text(json.dumps({"version": 2, "cards": merged}, ensure_ascii=False,
                                        indent=2), encoding="utf-8")

        registry = [p for p in load_playlists() if p.get("id") != playlist_id]
        registry.insert(0, {"id": playlist_id, "title": job.title,
                            "nb_tracks": job.total, "cards": len(new_cards),
                            "imported_at": time.strftime("%Y-%m-%dT%H:%M:%S")})
        save_playlists(registry)
        if review:
            review_file = POOL_FILE.parent / "pool-review.txt"
            review_file.write_text("\n".join(review) + "\n", encoding="utf-8")

        job.added = len(new_cards)
        job.skipped = {k: v for k, v in skipped.items() if v}
        job.state = "done"
        job.message = f"{len(new_cards)} Karten uebernommen"
    except Exception as exc:                      # Job-Fehler gehoert in die UI, nicht ins Log
        job.state = "error"
        job.message = f"{type(exc).__name__}: {exc}"
    finally:
        job.finished = time.time()
    return job


async def import_in_background(playlist_id: str) -> Job:
    job = Job(id=secrets.token_urlsafe(6), playlist_id=playlist_id)
    JOBS[job.id] = job
    asyncio.create_task(import_playlist(playlist_id, job))
    return job
