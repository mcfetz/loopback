"""Tests fuer die persistente Highscore-Liste."""

from __future__ import annotations

import json
import sys
from pathlib import Path
from types import SimpleNamespace

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from app import scores


def use_tmp(monkeypatch, tmp_path):
    monkeypatch.setattr(scores, "DATA_DIR", tmp_path)
    monkeypatch.setattr(scores, "SCORES_FILE", tmp_path / "highscores.json")


def test_empty_list_without_file(monkeypatch, tmp_path):
    use_tmp(monkeypatch, tmp_path)
    assert scores.top() == []


def test_record_and_order(monkeypatch, tmp_path):
    use_tmp(monkeypatch, tmp_path)
    scores.record("Anna", 300, 2, 10, False)
    scores.record("Ben", 900, 6, 10, False)
    scores.record("Clara", 600, 4, 5, True)
    rows = scores.top()
    assert [r["name"] for r in rows] == ["Ben", "Clara", "Anna"]
    assert rows[0]["points"] == 900
    assert rows[1]["solo"] is True


def test_best_run_per_name_wins(monkeypatch, tmp_path):
    use_tmp(monkeypatch, tmp_path)
    scores.record("Anna", 500, 3, 10, False)
    scores.record("Anna", 200, 1, 5, False)      # schlechter -> bleibt bei 500
    assert scores.top()[0]["points"] == 500
    scores.record("Anna", 700, 5, 15, False)     # besser -> ersetzt
    rows = scores.top()
    assert len(rows) == 1
    assert rows[0]["points"] == 700 and rows[0]["rounds"] == 15


def test_survives_restart(monkeypatch, tmp_path):
    use_tmp(monkeypatch, tmp_path)
    scores.record("Anna", 420, 3, 10, False)
    # neuer Prozess = frisch von Platte lesen
    raw = json.loads((tmp_path / "highscores.json").read_text())
    assert raw["scores"][0]["name"] == "Anna"
    assert scores.top(limit=5)[0]["points"] == 420


def test_limit_and_broken_file(monkeypatch, tmp_path):
    use_tmp(monkeypatch, tmp_path)
    for i in range(12):
        scores.record(f"Spieler {i}", i * 10, i, 10, False)
    assert len(scores.top(limit=3)) == 3
    assert len(scores.top()) == 10
    (tmp_path / "highscores.json").write_text("{kaputt")
    assert scores.top() == []          # kaputte Datei -> leere Liste statt Absturz


def test_record_room_skips_empty_players(monkeypatch, tmp_path):
    use_tmp(monkeypatch, tmp_path)
    players = {
        "a": SimpleNamespace(name="Anna", points=600, score=4),
        "b": SimpleNamespace(name="Ben", points=0, score=1),     # nichts gerissen
    }
    room = SimpleNamespace(players=players, round=10, solo=False)
    entries = scores.record_room(room)
    assert [e["name"] for e in entries] == ["Anna"]
    assert entries[0]["cards"] == 3          # Startkarte zaehlt nicht
    assert [r["name"] for r in scores.top()] == ["Anna"]
