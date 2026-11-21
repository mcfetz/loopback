"""Song-Timeline: Regeltests ohne Server.

Aufruf:  python -m pytest tests -q
"""

from __future__ import annotations

import random
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from app.game import Card, GameError, Room, TARGET_CARDS, slot_is_correct


def make_pool(n: int = 20) -> list[Card]:
    return [Card(key=f"c{i}", title=f"Titel {i}", artist=f"Band {i}",
                 year=1960 + i, deezer_id=str(1000 + i)) for i in range(n)]


def make_room(n_players: int = 2) -> tuple[Room, list[str]]:
    room = Room.create(make_pool(), host_id="host", code="ABCDE", rng=random.Random(7))
    pids = []
    for i in range(n_players):
        room.add_player(f"p{i}", f"Spieler {i}")
        pids.append(f"p{i}")
    return room, pids


def test_slot_is_correct_edges():
    tl = [Card("a", "A", "X", 1970, "1"), Card("b", "B", "X", 1990, "2")]
    assert slot_is_correct(tl, 0, Card("n", "N", "X", 1960, "3"))
    assert slot_is_correct(tl, 1, Card("n", "N", "X", 1980, "3"))
    assert slot_is_correct(tl, 2, Card("n", "N", "X", 2000, "3"))
    assert not slot_is_correct(tl, 0, Card("n", "N", "X", 1980, "3"))
    assert not slot_is_correct(tl, 2, Card("n", "N", "X", 1980, "3"))
    assert not slot_is_correct(tl, 5, Card("n", "N", "X", 1980, "3"))
    # Gleichstand ist erlaubt (gleiches Jahr links oder rechts)
    assert slot_is_correct(tl, 1, Card("n", "N", "X", 1970, "3"))


def test_everyone_starts_with_the_seed_card():
    room, pids = make_room(3)
    for pid in pids:
        assert room.players[pid].timeline == [room.seed]
    assert len({room.players[p].timeline[0].key for p in pids}) == 1


def test_correct_placement_keeps_the_card_and_scores():
    room, pids = make_room(1)
    room.start()
    card = room.next_round()
    player = room.players["p0"]
    slot = 1 if card.year >= player.timeline[0].year else 0
    room.place("p0", slot)
    results = room.reveal()
    assert results[0]["correct"] is True
    assert player.score == 2
    assert card in player.timeline


def test_wrong_placement_costs_the_card():
    room, pids = make_room(1)
    room.start()
    card = room.next_round()
    player = room.players["p0"]
    wrong = 0 if card.year >= player.timeline[0].year else 1
    room.place("p0", wrong)
    results = room.reveal()
    assert results[0]["correct"] is False
    assert player.score == 1
    assert card not in player.timeline


def test_no_guess_counts_as_wrong():
    room, pids = make_room(1)
    room.start()
    room.next_round()
    results = room.reveal()
    assert results[0]["correct"] is False
    assert room.players["p0"].score == 1


def test_timeline_stays_sorted_when_cards_are_added():
    room, pids = make_room(1)
    room.start()
    player = room.players["p0"]
    for _ in range(6):
        card = room.next_round()
        years = [c.year for c in player.timeline]
        slot = sum(1 for y in years if y <= card.year)
        room.place("p0", slot)
        room.reveal()
    years = [c.year for c in player.timeline]
    assert years == sorted(years)
    assert len(years) == 7


def test_round_flow_rules():
    room, pids = make_room(1)
    room.start()
    room.next_round()
    try:
        room.next_round()
        raise AssertionError("zweite Karte ohne Reveal muss scheitern")
    except GameError:
        pass
    room.reveal()
    room.next_round()          # nach Reveal erlaubt
    try:
        room.place("p0", 99)
        raise AssertionError("ungueltiger Slot muss scheitern")
    except GameError:
        pass


def test_win_at_target_cards():
    room, _ = make_room(1)
    player = room.players["p0"]
    # kuenstlich nah an die Zielmarke bringen
    extra = [Card(f"x{i}", f"X{i}", "Y", 1900 + i, str(9000 + i)) for i in range(TARGET_CARDS - 2)]
    player.timeline = extra + player.timeline
    room.start()
    card = room.next_round()
    years = [c.year for c in player.timeline]
    room.place("p0", sum(1 for y in years if y <= card.year))
    room.reveal()
    assert player.score == TARGET_CARDS
    assert room.state == "finished"
    assert room.winner == player.name


def test_duplicate_names_and_late_join_are_rejected():
    room, _ = make_room(1)
    try:
        room.add_player("p9", "Spieler 0")
        raise AssertionError("doppelter Name muss scheitern")
    except GameError:
        pass
    room.start()
    try:
        room.add_player("p9", "Zuspaet")
        raise AssertionError("Beitritt nach Start muss scheitern")
    except GameError:
        pass


def test_snapshot_hides_the_solution():
    room, pids = make_room(1)
    room.start()
    card = room.next_round()
    room.preview = "https://example.invalid/preview.mp3"
    room.place("p0", 0)
    player_view = room.snapshot(pid="p0", is_host=False)
    host_view = room.snapshot(pid="host", is_host=True)
    assert "current" not in player_view                # Spieler bekommt kein Audio, keine Karte
    assert host_view["current"]["preview"]             # Host hat das Audio ...
    assert "year" not in host_view["current"]          # ... aber nicht die Loesung
    assert "title" not in host_view["current"]
    room.reveal()
    assert room.snapshot(pid="p0", is_host=False)["current"]["year"] == card.year
    assert room.snapshot(pid="host", is_host=True)["current"]["title"] == card.title


def test_skip_discards_the_card_without_scoring():
    room, _ = make_room(1)
    room.start()
    card = room.next_round()
    room.place("p0", 0)
    room.skip()
    assert room.state == "revealed"
    assert room.current is None
    assert room.players["p0"].score == 1
    assert room.log[-1]["skipped"] is True
    assert card not in room.players["p0"].timeline


def test_reset_returns_to_lobby():
    room, pids = make_room(1)
    room.start()
    room.next_round()
    room.place("p0", 0)
    room.reveal()
    room.reset()
    assert room.state == "lobby"
    assert room.players["p0"].timeline == [room.seed]
    assert room.players["p0"].pending is None


def test_pool_too_small_is_refused():
    try:
        Room.create(make_pool(5), host_id="h", code="X")
        raise AssertionError("zu kleiner Pool muss scheitern")
    except GameError:
        pass
