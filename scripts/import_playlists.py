"""Kartenpool aus Deezer-Playlists aufbauen.

Das Jahr ist im Spiel die einzige Wahrheit, und Deezer kennt nur das Album-Datum
("ABBA Gold" -> Dancing Queen 2005). Deshalb kommt das Jahr aus der Apple-Music-Suche
(iTunes-API): die nennt pro Track das Erstveroeffentlichungsdatum, auch bei Compilations
(nachgemessen: Dancing Queen 1976, True Colors 1986, Wonderwall 1995). Deezer liefert
weiter Audio-Preview und Genres, Apple nur das Jahr.

Aufruf:
    python scripts/import_playlists.py 12204287271 [weitere ids oder URLs] [--limit N]
    python scripts/import_playlists.py --no-merge 12204287271     # kuratierten Pool ersetzen

Geschrieben wird data/pool.json (kuratierte Karten bleiben und gewinnen beim Jahr) und
data/pool-review.txt mit allen Karten, deren Jahr unsicher ist.
"""

from __future__ import annotations

import argparse
import asyncio
import json
import re
import sys
import urllib.parse
import urllib.request
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import httpx

import build_pool as bp
from app.deezer import DEEZER, POOL_FILE, UA

ITUNES = "https://itunes.apple.com/search"
REVIEW_FILE = POOL_FILE.parent / "pool-review.txt"
PLAYLIST_RE = re.compile(r"playlist/(\d+)")
MIN_SECONDS = 75          # keine Skits/Intros als Karte
YEAR_TOLERANCE = 2        # Abweichung Apple <-> Deezer, ab der eine Karte auf die Pruefliste kommt


# ------------------------------------------------------------------ Deezer

def parse_playlists(args: list[str]) -> list[str]:
    ids = []
    for raw in args:
        hit = PLAYLIST_RE.search(raw)
        ids.append(hit.group(1) if hit else raw.strip())
    return ids


async def fetch_playlist(client: httpx.AsyncClient, pid: str) -> tuple[dict, list[dict]]:
    meta = await bp.fetch(client, f"playlist/{pid}") or {}
    await asyncio.sleep(0.16)
    if not meta.get("id"):
        raise SystemExit(f"Playlist {pid} nicht lesbar (geloescht oder nicht oeffentlich?)")
    tracks: list[dict] = []
    index = 0
    while True:
        page = await bp.fetch(client, f"playlist/{pid}/tracks", limit=100, index=index) or {}
        await asyncio.sleep(0.16)
        data = page.get("data") or []
        if not data:
            break
        tracks.extend(data)
        index += len(data)
        if len(tracks) >= (meta.get("nb_tracks") or 0):
            break
    return meta, tracks


async def album_genres(client: httpx.AsyncClient, album_id) -> list[str]:
    if not album_id:
        return []
    album = await bp.fetch(client, f"album/{album_id}") or {}
    await asyncio.sleep(0.16)
    raw = [g.get("name") for g in (album.get("genres") or {}).get("data") or [] if g.get("name")]
    return raw


# ------------------------------------------------------------------ Jahr via Apple Music

def itunes_year(title: str, artist: str, results: list[dict]) -> int | None:
    """Jahr aus den Apple-Treffern: haeufigstes Jahr der passenden Fassungen.

    Nicht das frueheste: ein einzelner Fehleintrag mit kaputtem Datum reisst sonst alles nach
    unten (Apple fuehrt "Paradise By the Dashboard Light (Radio Edit)" mit 1966, richtig ist
    1977 — drei korrekte Eintraege gegen einen falschen). Haeufigstes Jahr, bei Gleichstand
    das fruehere.
    """
    want_title = bp.norm(title)
    want_artist = bp.norm(artist)
    years: list[int] = []
    for res in results:
        got_title = bp.norm(res.get("trackName"))
        got_artist = bp.norm(res.get("artistName"))
        if not got_title or not got_artist:
            continue
        if want_artist not in got_artist and got_artist not in want_artist:
            continue
        if not (want_title in got_title or got_title in want_title):
            continue
        if not bp.is_clean(res.get("trackName")) or "radio edit" in got_title:
            continue
        year = (res.get("releaseDate") or "")[:4]
        if year.isdigit():
            years.append(int(year))
    if not years:
        return None
    counts = {}
    for year in years:
        counts[year] = counts.get(year, 0) + 1
    best = max(counts.values())
    return min(year for year, count in counts.items() if count == best)



async def apple_lookup(title: str, artist: str, retries: int = 3) -> int | None:
    # limit=25 statt 8: bei "Tequila" liefert Apple mit 8 Treffern nur das 2026er Sessions-Album,
    # die Originalaufnahmen von 1958 tauchen erst weiter hinten auf.
    query = urllib.parse.urlencode({"term": f"{title} {artist}", "entity": "song",
                                   "limit": 25, "country": "DE"})
    url = f"{ITUNES}?{query}"
    for attempt in range(retries):
        try:
            req = urllib.request.Request(url, headers={"User-Agent": UA})
            with urllib.request.urlopen(req, timeout=15) as resp:
                data = json.loads(resp.read().decode("utf-8", "replace"))
            return itunes_year(title, artist, data.get("results") or [])
        except Exception:
            await asyncio.sleep(1.5 * (attempt + 1))
    return None


# ------------------------------------------------------------------ Hauptlauf

def existing_curated() -> list[dict]:
    if not POOL_FILE.exists():
        return []
    data = json.loads(POOL_FILE.read_text(encoding="utf-8"))
    cards = data["cards"] if isinstance(data, dict) else data
    return [c for c in cards if c.get("year_source") in (None, "kuratiert")]


def same_song(a: dict, b: dict) -> bool:
    """Grob gleicher Song: Interpret passt, ein Titel steckt im anderen."""
    if bp.norm(a["artist"]) != bp.norm(b["artist"]):
        return False
    ta, tb = bp.norm(a["title"]), bp.norm(b["title"])
    return bool(ta) and bool(tb) and (ta in tb or tb in ta)


async def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("playlists", nargs="+", help="Playlist-IDs oder Deezer-Links")
    ap.add_argument("--limit", type=int, default=0, help="nur die ersten N Karten (Test)")
    ap.add_argument("--no-merge", action="store_true", help="kuratierten Pool nicht uebernehmen")
    args = ap.parse_args()

    curated = [] if args.no_merge else existing_curated()
    print(f"Kuratierte Karten bleiben: {len(curated)}")

    cards: list[dict] = list(curated)
    review: list[str] = []
    skipped = {"kein_preview": 0, "schlechte_fassung": 0, "zu_kurz": 0, "doppelt": 0,
               "kein_jahr": 0, "kuratiert_vorhanden": 0}
    seen_isrc: set[str] = set()
    year_from_apple = 0
    year_from_deezer = 0

    async with httpx.AsyncClient(timeout=15.0, headers={"User-Agent": UA}) as client:
        for pid in parse_playlists(args.playlists):
            meta, tracks = await fetch_playlist(client, pid)
            print(f"\nPlaylist {pid}: {meta.get('title')!r} — {len(tracks)} Tracks "
                  f"(laut Deezer {meta.get('nb_tracks')})")
            for track in tracks:
                if args.limit and len(cards) >= len(curated) + args.limit:
                    break
                title = (track.get("title") or "").strip()
                artist = ((track.get("artist") or {}).get("name") or "").strip()
                isrc = (track.get("isrc") or "").strip()
                if not title or not artist:
                    continue
                if not track.get("preview"):
                    skipped["kein_preview"] += 1
                    continue
                if not bp.is_clean(title):
                    skipped["schlechte_fassung"] += 1
                    continue
                if (track.get("duration") or 0) < MIN_SECONDS:
                    skipped["zu_kurz"] += 1
                    continue
                if isrc and isrc in seen_isrc:
                    skipped["doppelt"] += 1
                    continue
                candidate = {"title": title, "artist": artist}
                if any(same_song(candidate, c) for c in cards):
                    skipped["kuratiert_vorhanden"] += 1
                    continue

                album = track.get("album") or {}
                deezer_year = (album.get("release_date") or "")[:4]
                deezer_year = int(deezer_year) if deezer_year.isdigit() else None

                apple_year = await apple_lookup(title, artist)
                await asyncio.sleep(0.4)

                year = apple_year or deezer_year
                if year is None:
                    skipped["kein_jahr"] += 1
                    review.append(f"KEIN JAHR: {title} — {artist}")
                    continue
                source = "itunes" if apple_year else "deezer-album"
                if apple_year:
                    year_from_apple += 1
                else:
                    year_from_deezer += 1
                if apple_year and deezer_year and abs(apple_year - deezer_year) > YEAR_TOLERANCE:
                    review.append(f"JAHR ABWEICHEND: {title} — {artist} | Apple {apple_year} | "
                                  f"Deezer-Album {deezer_year} ({album.get('title')})")

                raw_genres = await album_genres(client, album.get("id"))
                genres = bp.bucket_genres(raw_genres, artist)
                if not genres:
                    # Ohne Album-Genre trotzdem filterbar halten, statt es stumm zu verlieren.
                    genres = ["Sonstige"]
                    review.append(f"OHNE GENRE (als Sonstige gefuehrt): {title} — {artist}")

                if isrc:
                    seen_isrc.add(isrc)
                cards.append({
                    "key": bp.slug(title, artist),
                    "title": title,
                    "artist": artist,
                    "year": year,
                    "deezer_id": str(track["id"]),
                    "deezer_release_year": str(deezer_year) if deezer_year else None,
                    "preview_ok": True,
                    "genres": genres,
                    "deezer_genres": raw_genres,
                    "isrc": isrc or None,
                    "year_source": source,
                    "playlist": pid,
                })
                if len(cards) % 25 == 0:
                    print(f"  ... {len(cards)} Karten")

    # Dubletten nach Key zusammenfassen (gleicher Song aus zwei Playlists)
    unique: list[dict] = []
    keys: set[str] = set()
    for card in cards:
        if card.get("year_source") in (None, "kuratiert"):
            unique.append(card)
            keys.add(card["key"])
            continue
        if card["key"] in keys:
            continue
        keys.add(card["key"])
        unique.append(card)

    POOL_FILE.parent.mkdir(parents=True, exist_ok=True)
    POOL_FILE.write_text(json.dumps({"version": 2, "cards": unique}, ensure_ascii=False, indent=2),
                         encoding="utf-8")
    if review:
        REVIEW_FILE.write_text("\n".join(review) + "\n", encoding="utf-8")

    print(f"\n{len(unique)} Karten geschrieben -> {POOL_FILE}")
    print(f"Jahre aus Apple Music: {year_from_apple} | nur Deezer-Album: {year_from_deezer}")
    print("Uebersprungen:", ", ".join(f"{k}={v}" for k, v in skipped.items() if v))
    if review:
        print(f"Pruefliste ({len(review)} Zeilen) -> {REVIEW_FILE}")
    return 0


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))
