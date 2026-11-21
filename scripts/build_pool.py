"""Kartenpool bauen: kuratierte Jahre bleiben die Wahrheit, Deezer liefert nur die Track-ID.

Warum so: `track.release_date` ist das Release-Datum des *Albums*. Fuer "Dancing Queen"
liefert Deezer 2005-10-07 (ABBA Gold), richtig ist 1976. Fuer ein Ratespiel ist das
Gift, deshalb stehen die Jahre hier handgeprueft in der Liste und Deezer wird nur fuer
ID und Preview gebraucht. Das Skript meldet jede Abweichung, damit man sie einzeln
anschauen kann.

Aufruf:  python scripts/build_pool.py
"""

from __future__ import annotations

import asyncio
import json
import re
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import httpx

from app.deezer import POOL_FILE, UA, DEEZER

# (Titel, Interpret, Originaljahr) — Jahr = Erstveroeffentlichung des Songs, nicht des Albums
CARDS: list[tuple[str, str, int]] = [
    ("Hound Dog", "Elvis Presley", 1956),
    ("Johnny B. Goode", "Chuck Berry", 1958),
    ("Hit the Road Jack", "Ray Charles", 1961),
    ("She Loves You", "The Beatles", 1963),
    ("You Really Got Me", "The Kinks", 1964),
    ("(I Can't Get No) Satisfaction", "The Rolling Stones", 1965),
    ("My Generation", "The Who", 1965),
    ("I Got You (I Feel Good)", "James Brown", 1965),
    ("Like a Rolling Stone", "Bob Dylan", 1965),
    ("Hey Joe", "Jimi Hendrix", 1966),
    ("Light My Fire", "The Doors", 1967),
    ("Respect", "Aretha Franklin", 1967),
    ("Mrs. Robinson", "Simon & Garfunkel", 1968),
    ("Fortunate Son", "Creedence Clearwater Revival", 1969),
    ("I Want You Back", "The Jackson 5", 1969),
    ("What's Going On", "Marvin Gaye", 1971),
    ("Stairway to Heaven", "Led Zeppelin", 1971),
    ("Superstition", "Stevie Wonder", 1972),
    ("No Woman, No Cry", "Bob Marley", 1974),
    ("Bohemian Rhapsody", "Queen", 1975),
    ("Dancing Queen", "ABBA", 1976),
    ("Hotel California", "Eagles", 1976),
    ("Dreams", "Fleetwood Mac", 1977),
    ("Stayin' Alive", "Bee Gees", 1977),
    ("Heroes", "David Bowie", 1977),
    ("I Feel Love", "Donna Summer", 1977),
    ("Das Model", "Kraftwerk", 1978),
    ("London Calling", "The Clash", 1979),
    ("Another Brick in the Wall", "Pink Floyd", 1979),
    ("Don't Stop Believin'", "Journey", 1981),
    ("Africa", "Toto", 1982),
    ("Billie Jean", "Michael Jackson", 1983),
    ("Every Breath You Take", "The Police", 1983),
    ("Sweet Dreams (Are Made of This)", "Eurythmics", 1983),
    ("Blue Monday", "New Order", 1983),
    ("99 Luftballons", "Nena", 1983),
    ("Major Tom (Völlig losgelöst)", "Peter Schilling", 1982),
    ("Purple Rain", "Prince", 1984),
    ("Forever Young", "Alphaville", 1984),
    ("Männer", "Herbert Grönemeyer", 1984),
    ("Take On Me", "a-ha", 1985),
    ("Rock Me Amadeus", "Falco", 1985),
    ("Livin' on a Prayer", "Bon Jovi", 1986),
    ("Sweet Child O' Mine", "Guns N' Roses", 1987),
    ("Never Gonna Give You Up", "Rick Astley", 1987),
    ("I Wanna Dance with Somebody", "Whitney Houston", 1987),
    ("Like a Prayer", "Madonna", 1989),
    ("Personal Jesus", "Depeche Mode", 1989),
    ("Smells Like Teen Spirit", "Nirvana", 1991),
    ("Under the Bridge", "Red Hot Chili Peppers", 1991),
    ("Enter Sandman", "Metallica", 1991),
    ("Creep", "Radiohead", 1992),
    ("Schrei nach Liebe", "Die Ärzte", 1993),
    ("Wonderwall", "Oasis", 1995),
    ("Firestarter", "The Prodigy", 1996),
    ("Song 2", "Blur", 1997),
    ("Du hast", "Rammstein", 1997),
    ("...Baby One More Time", "Britney Spears", 1998),
    ("Believe", "Cher", 1998),
    ("Lose Yourself", "Eminem", 2002),
    ("Hurt", "Johnny Cash", 2002),
    ("Hey Ya!", "OutKast", 2003),
    ("Rehab", "Amy Winehouse", 2006),
    ("Umbrella", "Rihanna", 2007),
    ("Bad Romance", "Lady Gaga", 2009),
    ("Rolling in the Deep", "Adele", 2010),
    ("Easy", "Cro", 2012),
    ("Kids", "Marteria", 2013),
    ("Leider geil", "Deichkind", 2012),
    ("Songs für Liam", "Kraftklub", 2012),
    ("Get Lucky", "Daft Punk", 2013),
    ("Uptown Funk", "Mark Ronson", 2014),
    ("Shape of You", "Ed Sheeran", 2017),
    ("bad guy", "Billie Eilish", 2019),
    ("Blinding Lights", "The Weeknd", 2019),
    ("Don't Start Now", "Dua Lipa", 2019),
    ("Roller", "Apache 207", 2019),
]


def slug(title: str, artist: str) -> str:
    raw = f"{artist}-{title}".lower()
    return re.sub(r"[^a-z0-9]+", "-", raw).strip("-")[:60]


async def fetch(client: httpx.AsyncClient, path: str, **params) -> dict | None:
    try:
        resp = await client.get(f"{DEEZER}/{path}", params=params or None)
        resp.raise_for_status()
        return resp.json()
    except (httpx.HTTPError, ValueError):
        return None


async def build() -> int:
    cards: list[dict] = []
    problems: list[str] = []
    seen: set[str] = set()

    async with httpx.AsyncClient(timeout=12.0, headers={"User-Agent": UA}) as client:
        for title, artist, year in CARDS:
            hit = (await fetch(client, "search", q=f"{title} {artist}", limit=5) or {}).get("data") or []
            await asyncio.sleep(0.16)
            if not hit:
                problems.append(f"KEIN TREFFER: {title} — {artist} ({year})")
                continue
            track = hit[0]
            detail = await fetch(client, f"track/{track['id']}") or {}
            await asyncio.sleep(0.16)
            if str(detail.get("id") or track["id"]) in seen:
                problems.append(f"DOPPELT: {title} — {artist} -> {track['id']}")
                continue
            seen.add(str(track["id"]))
            album_year = (detail.get("album") or {}).get("release_date") or ""
            got = album_year[:4]
            cards.append({
                "key": slug(title, artist),
                "title": detail.get("title") or track.get("title") or title,
                "artist": (detail.get("artist") or {}).get("name") or artist,
                "year": year,
                "deezer_id": str(track["id"]),
                "deezer_release_year": got or None,
                "preview_ok": bool(detail.get("preview")),
            })
            if not detail.get("preview"):
                problems.append(f"KEIN PREVIEW: {title} — {artist}")
            if got and got != str(year):
                problems.append(f"JAHR ABWEICHEND: {title} — {artist} | kuratiert {year} | Deezer-Album {got}")

    POOL_FILE.parent.mkdir(parents=True, exist_ok=True)
    POOL_FILE.write_text(json.dumps({"version": 1, "cards": cards}, ensure_ascii=False, indent=2),
                         encoding="utf-8")
    print(f"{len(cards)} Karten geschrieben -> {POOL_FILE}")
    if problems:
        print("\nZu pruefen:")
        for line in problems:
            print(" ", line)
    return 0


if __name__ == "__main__":
    raise SystemExit(asyncio.run(build()))
