"""Persistente Highscore-Liste.

Eine Zeile pro Name (die beste Partie), abgelegt in ``data/highscores.json``. Bewusst kein
SQLite: die Datei bleibt klein, ist lesbar, landet im Backup und ein Absturz kann sie dank
atomarem Schreiben (tmp + rename) nicht zerschlagen.
"""

from __future__ import annotations

import json
import logging
import os
import threading
import time
from pathlib import Path

log = logging.getLogger("loopback")

# Im Container zeigt das auf ein Volume (LOOPBACK_DATA_DIR), sonst neben das Projekt.
DATA_DIR = Path(os.environ.get("LOOPBACK_DATA_DIR") or
                Path(__file__).resolve().parent.parent / "data").resolve()
SCORES_FILE = DATA_DIR / "highscores.json"
MAX_ENTRIES = 50

_lock = threading.Lock()


def _load() -> list[dict]:
    try:
        raw = json.loads(SCORES_FILE.read_text(encoding="utf-8"))
    except FileNotFoundError:
        return []
    except (OSError, json.JSONDecodeError) as exc:
        log.warning("Highscore-Datei unlesbar (%s) — starte mit leerer Liste", exc)
        return []
    rows = raw.get("scores") if isinstance(raw, dict) else raw
    return [r for r in (rows or []) if isinstance(r, dict) and r.get("name")]


def _save(rows: list[dict]) -> None:
    DATA_DIR.mkdir(parents=True, exist_ok=True)
    tmp = SCORES_FILE.with_suffix(".tmp")
    tmp.write_text(json.dumps({"scores": rows}, ensure_ascii=False, indent=2), encoding="utf-8")
    tmp.replace(SCORES_FILE)      # atomar: entweder alte oder neue Liste, nie eine halbe


def _sort_key(row: dict) -> tuple:
    return (-int(row.get("points", 0)), -int(row.get("cards", 0)), str(row.get("name", "")))


def top(limit: int = 10) -> list[dict]:
    rows = sorted(_load(), key=_sort_key)
    return rows[: max(1, int(limit))]


def record(name: str, points: int, cards: int, rounds: int, solo: bool) -> dict:
    """Traegt eine Partie ein — pro Name bleibt die beste stehen."""
    entry = {
        "name": str(name)[:24] or "Unbekannt",
        "points": int(points),
        "cards": int(cards),
        "rounds": int(rounds),
        "solo": bool(solo),
        "at": time.strftime("%Y-%m-%d %H:%M"),
    }
    with _lock:
        rows = _load()
        existing = next((r for r in rows if r.get("name") == entry["name"]), None)
        if existing is not None:
            better = (entry["points"], entry["cards"]) > (int(existing.get("points", 0)),
                                                          int(existing.get("cards", 0)))
            if not better:
                return existing
            existing.update(entry)
        else:
            rows.append(entry)
        rows.sort(key=_sort_key)
        _save(rows[:MAX_ENTRIES])
    return entry


def record_room(room) -> list[dict]:
    """Alle Spieler eines beendeten Raums eintragen (wer nichts gerissen hat, bleibt draussen)."""
    entries = []
    for player in room.players.values():
        cards = max(0, player.score - 1)      # die Startkarte war geschenkt
        if player.points <= 0 and cards <= 0:
            continue
        entries.append(record(player.name, player.points, cards, room.round, room.solo))
    return entries
