"""Tests fuer Pool-Logik und Playlist-Import (ohne Netz)."""

from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from app import catalog, importer


def test_parse_playlist_id():
    assert importer.parse_playlist_id("12204287271") == "12204287271"
    assert importer.parse_playlist_id("https://www.deezer.com/de/playlist/12204287271?host=1") \
        == "12204287271"
    assert importer.parse_playlist_id("https://link.deezer.com/s/abc") is None
    assert importer.parse_playlist_id("   ") is None


def test_short_link_is_not_parsed_directly_but_resolved():
    # Kurzlinks tragen keine ID; sie brauchen den HTTP-Redirect (resolve_playlist_id).
    short = "https://link.deezer.com/s/351wa9lZdHsgXgzIjKtuv"
    assert importer.parse_playlist_id(short) is None


def test_is_clean_rejects_live_but_not_alive():
    assert catalog.is_clean("Dancing Queen")
    assert not catalog.is_clean("She Loves You (Live)")
    assert not catalog.is_clean("You Really Got Me (Live From The Playhouse Theatre)")
    assert catalog.is_clean("Stayin Alive")              # "alive" ist kein "live"
    assert not catalog.is_clean("Firestarter (Instrumental)")
    assert not catalog.is_clean("Jailhouse Rock / Hound Dog / Teddy Bear")
    assert not catalog.is_clean("Leider geil (Solomun Remix)")


def test_itunes_year_uses_the_most_common_year():
    # Meat Loaf: ein kaputter Eintrag mit 1966, drei richtige mit 1977
    results = [
        {"trackName": "Paradise By the Dashboard Light", "artistName": "Meat Loaf",
         "releaseDate": "1966-01-01T07:00:00Z"},
        {"trackName": "Paradise By the Dashboard Light", "artistName": "Meat Loaf & Ellen Foley",
         "releaseDate": "1977-10-21T07:00:00Z"},
        {"trackName": "Paradise By the Dashboard Light", "artistName": "Meat Loaf",
         "releaseDate": "1977-10-21T07:00:00Z"},
        {"trackName": "Paradise By the Dashboard Light", "artistName": "Meat Loaf",
         "releaseDate": "1977-10-21T07:00:00Z"},
    ]
    assert catalog.itunes_year("Paradise By the Dashboard Light", "Meat Loaf", results) == 1977


def test_itunes_year_ignores_other_artists_and_radio_edits():
    results = [
        {"trackName": "Tequila", "artistName": "Some Cover Band", "releaseDate": "2019-01-01"},
        {"trackName": "Tequila (Radio Edit)", "artistName": "The Champs",
         "releaseDate": "2026-01-01"},
        {"trackName": "Tequila", "artistName": "The Champs", "releaseDate": "1958-06-23"},
    ]
    assert catalog.itunes_year("Tequila", "Champs", results) == 1958


def test_itunes_year_without_match():
    assert catalog.itunes_year("Unbekannt", "Niemand",
                               [{"trackName": "Anderes", "artistName": "Andere",
                                 "releaseDate": "2000-01-01"}]) is None


def test_bucket_genres_maps_and_adds_deutsch():
    assert catalog.bucket_genres(["Pop"], "ABBA") == ["Pop"]
    assert catalog.bucket_genres(["Rap/Hip Hop"], "Cro") == ["Deutsch", "Hip-Hop"]
    assert catalog.bucket_genres([], "Unbekannt") == []


def test_grouped_genres_counts_sorted():
    cards = [{"genres": ["Pop"]}, {"genres": ["Pop", "Rock"]}, {"genres": ["Rock"]}]
    assert catalog.grouped_genres(cards) == [{"name": "Pop", "count": 2},
                                             {"name": "Rock", "count": 2}]
