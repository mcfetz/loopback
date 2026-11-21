"""Gemeinsame Pool-Logik: Jahresermittlung, Genre-Bündel, Fassungsfilter.

Liegt im App-Paket, weil sowohl die CLI-Skripte (scripts/) als auch der Playlist-Import
zur Laufzeit (app/importer.py) dieselben Regeln brauchen.
"""

from __future__ import annotations

import json
import re
import urllib.parse
import urllib.request

from app.deezer import UA

ITUNES = "https://itunes.apple.com/search"

BAD_WORDS = ("instrumental", "karaoke", "cover", "tribute", "made famous", "in the style of",
             "commentary", "remix", "mixed", "medley")
LIVE_RE = re.compile(r"\blive\b")     # "(Live)" ja, "Stayin Alive" nein

# Deezer liefert Genres pro Album und sehr feinkoernig. Fuer eine Party-Filterung wird daraus
# eine Handvoll Schubladen.
GENRE_BUCKETS = {
    "pop": "Pop", "international pop": "Pop", "electro pop/electro rock": "Pop",
    "singer & songwriter": "Pop", "indie pop": "Pop", "chanson": "Pop",
    "rock": "Rock", "indie rock": "Rock", "alternative": "Rock", "heavy metal": "Rock",
    "hard rock": "Rock", "punk": "Rock", "metal": "Rock", "alternative rock": "Rock",
    "rap/hip hop": "Hip-Hop", "r&b": "Hip-Hop", "trap": "Hip-Hop", "hip hop": "Hip-Hop",
    "electro": "Electronic", "dance": "Electronic", "techno/house": "Electronic",
    "house": "Electronic", "disco": "Electronic", "trance": "Electronic",
    "soul & funk": "Soul & Funk", "soul": "Soul & Funk", "funk": "Soul & Funk",
    "filmmusik": "Film & Games", "filme/videospiele": "Film & Games",
    "soundtrack": "Film & Games", "musical": "Film & Games",
    "jazz": "Jazz & Blues", "blues": "Jazz & Blues",
    "folk": "Folk & Country", "country": "Folk & Country",
    "reggae": "Reggae", "klassik": "Klassik", "classical": "Klassik",
}

# Deutschsprachig ist kein Deezer-Genre, fuer eine deutsche Runde aber der naheliegendste Filter.
DEUTSCH = {
    "nena", "peter schilling", "herbert grönemeyer", "die ärzte", "rammstein", "cro",
    "marteria", "deichkind", "kraftklub", "apache 207", "falco", "fettes brot", "bap",
}

# Alben ohne Genre-Angabe (z. B. Live-Scheiben) bekommen hier ihr Genre.
MANUAL_GENRES = {"bob-marley-no-woman-no-cry": "Reggae"}

# Jahre, die Apple Music nicht findet und bei denen das Album-Datum nachweislich falsch ist
# (Remaster-/Jubilaeums-Scheiben). Schluessel = slug(Interpret, Titel) wie im Pool.
MANUAL_YEARS = {
    "rage-against-the-machine-killing-in-the-name": 1992,   # Album-Fallback sagte 2012
    "danzig-mother": 1988,                                  # 2009
    "limp-bizkit-behind-blue-eyes": 2003,                   # 2005
    "yngwie-malmsteen-rising-force": 1984,                  # 1988
    "maroon-5-this-love": 2002,                             # 2007
}

FALLBACK_GENRE = "Sonstige"

# Editions-Rauschen im Titel ("(2015 Remaster)", "(Single Version)", "(Deluxe Edition)"):
# fuer die Apple-Suche und die Anzeige stoert es nur — und verhindert das Finden des Originals.
NOISE_WORDS = (r"(?:remaster|remastered|version|edit|mono|stereo|deluxe|anniversary|bonus|"
               r"reissue)")
NOISE_RE = re.compile(
    r"\s*\([^)]*" + NOISE_WORDS + r"[^)]*\)"
    r"|\s*\[[^\]]*" + NOISE_WORDS + r"[^\]]*\]",
    re.IGNORECASE,
)


def search_title(title: str) -> str:
    """Titel ohne Editions-Zusatz, z. B. 'Wasted Years (2015 Remaster)' -> 'Wasted Years'."""
    cleaned = NOISE_RE.sub("", title or "").strip(" -–")
    return cleaned or (title or "").strip()


def norm(text: str) -> str:
    return re.sub(r"[^a-z0-9]+", " ", (text or "").lower().replace("ß", "ss")).strip()


def slug(title: str, artist: str) -> str:
    raw = f"{artist}-{title}".lower()
    return re.sub(r"[^a-z0-9]+", "-", raw).strip("-")[:60]


def is_clean(title: str) -> bool:
    """Brauchbare Fassung? Kein Medley, kein Live, kein Instrumental/Karaoke/Remix."""
    low = (title or "").lower()
    if " / " in low or "medley" in low:
        return False
    if LIVE_RE.search(low):
        return False
    return not any(bad in low for bad in BAD_WORDS)


def bucket_genres(raw: list[str], artist: str) -> list[str]:
    buckets = {GENRE_BUCKETS[g.lower()] for g in raw if g.lower() in GENRE_BUCKETS}
    if (artist or "").lower() in DEUTSCH:
        buckets.add("Deutsch")
    return sorted(buckets)


# ------------------------------------------------------------------ Jahr via Apple Music

def itunes_year(title: str, artist: str, results: list[dict]) -> int | None:
    """Haeufigstes Jahr unter den passenden Treffern, bei Gleichstand das fruehere.

    Nicht das frueheste: ein einzelner kaputter Eintrag reisst sonst alles nach unten
    (Apple fuehrt "Paradise By the Dashboard Light" einmal mit 1966, richtig ist 1977).
    """
    want_title, want_artist = norm(title), norm(artist)
    years: list[int] = []
    for res in results or []:
        got_title = norm(res.get("trackName"))
        got_artist = norm(res.get("artistName"))
        if not got_title or not got_artist:
            continue
        if want_artist not in got_artist and got_artist not in want_artist:
            continue
        if not (want_title in got_title or got_title in want_title):
            continue
        if not is_clean(res.get("trackName")) or "radio edit" in got_title:
            continue
        year = (res.get("releaseDate") or "")[:4]
        if year.isdigit():
            years.append(int(year))
    if not years:
        return None
    counts: dict[int, int] = {}
    for year in years:
        counts[year] = counts.get(year, 0) + 1
    best = max(counts.values())
    return min(year for year, count in counts.items() if count == best)


def _apple_request(params: dict) -> list[dict]:
    query = urllib.parse.urlencode(params)
    req = urllib.request.Request(f"{ITUNES}?{query}", headers={"User-Agent": UA})
    with urllib.request.urlopen(req, timeout=15) as resp:
        data = json.loads(resp.read().decode("utf-8", "replace"))
    return data.get("results") or []


def apple_year(title: str, artist: str) -> int | None:
    """Erstveroeffentlichungsjahr laut Apple Music (synchron).

    Zwei Anlaeufe: erst Titel+Interpret, dann nur der Titel. Bei bekannten Alben liefert Apple
    mit limit=25 sonst nur Nachbartracks — "American Idiot" von Green Day fehlte in den ersten
    25 Treffern komplett, die Albumtracks belegten die Liste.
    """
    query_title = search_title(title)
    attempts = [
        {"term": f"{query_title} {artist}", "entity": "song", "limit": 50, "country": "DE"},
        {"term": query_title, "entity": "song", "limit": 50, "country": "DE"},
    ]
    for params in attempts:
        for _ in range(2):
            try:
                year = itunes_year(query_title, artist, _apple_request(params))
            except Exception:
                continue
            if year:
                return year
    return None


def grouped_genres(cards: list[dict]) -> list[dict]:
    """Verfuegbare Genres mit Kartenzahl, haeufigste zuerst."""
    counts: dict[str, int] = {}
    for card in cards:
        for name in card.get("genres") or []:
            counts[name] = counts.get(name, 0) + 1
    return [{"name": name, "count": count}
            for name, count in sorted(counts.items(), key=lambda kv: (-kv[1], kv[0]))]
