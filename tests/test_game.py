"""Song-Timeline: Regeltests ohne Server.

Aufruf:  python -m pytest tests -q
"""

from __future__ import annotations

import random
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from app.game import (BASE_POINTS, FAST_SECONDS, MAX_MULTIPLIER, Card, GameError, Room,
                      TARGET_CARDS, filter_pool, slot_is_correct, speed_multiplier)


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


def test_solo_room_plays_a_full_round():
    room = Room.create(make_pool(), host_id="solo", code="SOLO1", rng=random.Random(3))
    room.solo = True
    room.add_player("solo", "Solo")
    room.start()
    assert room.state == "ready"
    card = room.next_round()
    player = room.players["solo"]
    assert room.state == "playing"
    years = [c.year for c in player.timeline]
    room.place("solo", sum(1 for y in years if y <= card.year))
    room.reveal()
    assert player.score == 2
    assert room.solo is True
    # Solo-Snapshot: Host-Sicht plus eigene Timeline
    snap = room.snapshot(pid="solo", is_host=True)
    assert snap["me"]["score"] == 2
    assert snap["current"]["year"] == card.year


def test_host_plays_along_and_still_does_not_see_the_answer():
    room = Room.create(make_pool(), host_id="host", code="HOST1", rng=random.Random(5))
    room.add_player("host", "Daniel")          # Host ist Spieler
    room.add_player("p1", "Anna")
    room.start()
    card = room.next_round()
    me = room.players["host"]
    years = [c.year for c in me.timeline]
    room.place("host", sum(1 for y in years if y <= card.year))

    snap = room.snapshot(pid="host", is_host=True)
    assert snap["me"]["pending"] is not None, "Host muss tippen koennen"
    assert "year" not in snap["current"], "Host darf die Loesung nicht vorher sehen"
    assert "title" not in snap["current"]

    results = room.reveal()
    assert len(results) == 2
    assert room.players["host"].score == 2
    assert room.snapshot(pid="host", is_host=True)["current"]["year"] == card.year


def test_filter_pool_by_genre():
    cards = [
        Card("a", "A", "X", 1970, "1", ("Rock",)),
        Card("b", "B", "X", 1980, "2", ("Pop", "Deutsch")),
        Card("c", "C", "X", 1990, "3", ()),
    ]
    assert len(filter_pool(cards, [])) == 3
    assert len(filter_pool(cards, None)) == 3
    assert [c.key for c in filter_pool(cards, ["Rock"])] == ["a"]
    assert [c.key for c in filter_pool(cards, ["pop"])] == ["b"]        # Gross-/Kleinschreibung egal
    assert sorted(c.key for c in filter_pool(cards, ["Deutsch", "Rock"])) == ["a", "b"]


def test_filtered_room_contains_only_matching_cards():
    rock = [Card(f"r{i}", f"R{i}", "A", 1970 + i, str(i), ("Rock",)) for i in range(12)]
    pop = [Card(f"p{i}", f"P{i}", "B", 1990 + i, str(100 + i), ("Pop",)) for i in range(12)]
    room = Room.create(filter_pool(rock + pop, ["Rock"]), host_id="h", code="F1",
                       rng=random.Random(1))
    assert all("Rock" in c.genres for c in room.deck + [room.seed])
    assert len(room.deck) + 1 == 12


def test_speed_multiplier_curve():
    assert speed_multiplier(None) == 1.0
    assert speed_multiplier(0) == MAX_MULTIPLIER
    assert speed_multiplier(FAST_SECONDS / 2) == 2.0
    assert speed_multiplier(FAST_SECONDS) == 1.0
    assert speed_multiplier(FAST_SECONDS + 10) == 1.0
    werte = [speed_multiplier(t) for t in (0, 2, 5, 10, 15, 20)]
    assert werte == sorted(werte, reverse=True), werte


def test_fast_guess_gets_full_multiplier():
    room, _ = make_room(1)
    room.start()
    card = room.next_round()
    player = room.players["p0"]
    years = [c.year for c in player.timeline]
    room.place("p0", sum(1 for y in years if y <= card.year))
    result = room.reveal()[0]
    assert result["correct"] is True
    assert result["multiplier"] == MAX_MULTIPLIER
    assert result["delta"] == round(BASE_POINTS * MAX_MULTIPLIER)
    assert player.points == result["delta"]


def test_slow_guess_gets_only_base_points():
    room, _ = make_room(1)
    room.start()
    card = room.next_round()
    player = room.players["p0"]
    years = [c.year for c in player.timeline]
    room.place("p0", sum(1 for y in years if y <= card.year))
    room.round_started -= 30          # Tipp kam erst nach 30 Sekunden
    result = room.reveal()[0]
    assert result["multiplier"] == 1.0
    assert result["delta"] == BASE_POINTS


def test_wrong_guess_scores_nothing():
    room, _ = make_room(1)
    room.start()
    card = room.next_round()
    player = room.players["p0"]
    wrong = 0 if card.year >= player.timeline[0].year else 1
    room.place("p0", wrong)
    result = room.reveal()[0]
    assert result["delta"] == 0
    assert player.points == 0


def test_winner_is_the_points_leader():
    room, _ = make_room(2)
    p0, p1 = room.players["p0"], room.players["p1"]
    room.start()
    # p0 hat schon 10 Karten (Spielende waere erreicht), aber keine Punkte
    p0.timeline = [Card(f"a{i}", "A", "X", 1960 + i, str(i)) for i in range(10)]
    p1.points = 900
    card = room.next_round()
    years = [c.year for c in p1.timeline]
    room.place("p1", sum(1 for y in years if y <= card.year))
    room.reveal()
    assert room.state == "finished"
    assert room.winner == p1.name


def test_no_guess_no_points():
    room, _ = make_room(1)
    room.start()
    room.next_round()
    result = room.reveal()[0]
    assert result["delta"] == 0
    assert result["multiplier"] == 1.0
    assert room.players["p0"].points == 0
