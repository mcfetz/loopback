"""Regelwerk fuer Song-Timeline: Raeume, Karten, Platzierungen.

Reine Logik ohne Netz und ohne FastAPI, damit tests/test_game.py sie ohne Server pruefen kann.
Der Server (app/main.py) haelt nur die Raeume und schiebt Snapshots an die Clients.
"""

from __future__ import annotations

import random
import secrets
from dataclasses import dataclass, field

TARGET_CARDS = 10   # wer so viele Karten in seiner Timeline hat, gewinnt
SEED_CARDS = 1      # jeder startet mit einer offenen Karte
CODE_ALPHABET = "ABCDEFGHJKLMNPQRSTUVWXYZ23456789"   # ohne O/0/I/1 (Verwechslung am Telefon)


class GameError(Exception):
    """Regelverstoss; der Server schickt den Text als {"type": "error"} an den Client."""


@dataclass(frozen=True)
class Card:
    key: str
    title: str
    artist: str
    year: int
    deezer_id: str


@dataclass
class Player:
    id: str
    name: str
    timeline: list[Card] = field(default_factory=list)
    pending: int | None = None      # Slot, den der Spieler getippt hat
    correct_last: bool | None = None
    connected: bool = True

    @property
    def score(self) -> int:
        return len(self.timeline)


def new_code(taken: set[str]) -> str:
    while True:
        code = "".join(secrets.choice(CODE_ALPHABET) for _ in range(5))
        if code not in taken:
            return code


def slot_is_correct(timeline: list[Card], slot: int, card: Card) -> bool:
    """Passt die Karte an diese Position? Timeline ist aufsteigend nach Jahr sortiert."""
    if not 0 <= slot <= len(timeline):
        return False
    left_ok = slot == 0 or timeline[slot - 1].year <= card.year
    right_ok = slot == len(timeline) or card.year <= timeline[slot].year
    return left_ok and right_ok


@dataclass
class Room:
    code: str
    host_id: str
    deck: list[Card]
    seed: Card
    players: dict[str, Player] = field(default_factory=dict)
    state: str = "lobby"            # lobby | playing | revealed | finished
    current: Card | None = None
    preview: str | None = None      # wird erst beim Reveal an alle gegeben
    round: int = 0
    results: list[dict] = field(default_factory=list)
    log: list[dict] = field(default_factory=list)
    winner: str | None = None

    # ---------- Aufbau ----------

    @classmethod
    def create(cls, pool: list[Card], host_id: str, code: str, rng: random.Random | None = None) -> Room:
        if len(pool) < TARGET_CARDS + 2:
            raise GameError(f"Kartenpool zu klein ({len(pool)} Karten, mindestens {TARGET_CARDS + 2} noetig)")
        rng = rng or random.Random()
        seed = rng.choice(pool)
        deck = [c for c in pool if c.key != seed.key]
        rng.shuffle(deck)
        return cls(code=code, host_id=host_id, deck=deck, seed=seed)

    def add_player(self, pid: str, name: str) -> Player:
        name = (name or "").strip()[:24]
        if not name:
            raise GameError("Name fehlt")
        if any(p.name.lower() == name.lower() for p in self.players.values()):
            raise GameError(f"'{name}' spielt schon mit")
        if self.state != "lobby":
            raise GameError("Spiel laeuft schon — Einstieg nur im Warteraum")
        player = Player(id=pid, name=name, timeline=[self.seed])
        self.players[pid] = player
        return player

    def reattach(self, pid: str) -> Player | None:
        player = self.players.get(pid)
        if player:
            player.connected = True
        return player

    # ---------- Ablauf ----------

    def start(self) -> None:
        if self.state != "lobby":
            raise GameError("Spiel laeuft schon")
        if not self.players:
            raise GameError("Kein Spieler im Raum")
        self.state = "ready"        # gestartet, aber noch keine Karte gezogen
        self.round = 0
        self.log = []
        self.results = []
        self.winner = None
        self.current = None
        self.preview = None

    def next_round(self) -> Card:
        if self.state not in ("lobby", "ready", "revealed"):
            raise GameError("Erst aufloesen, dann die naechste Karte")
        if self.state == "lobby":
            self.start()
        if not self.deck:
            self._finish()
            raise GameError("Kartenpool ist leer")
        self.current = self.deck.pop()
        self.preview = None
        self.round += 1
        self.state = "playing"
        self.results = []
        for player in self.players.values():
            player.pending = None
            player.correct_last = None
        return self.current

    def place(self, pid: str, slot: int) -> None:
        if self.state != "playing":
            raise GameError("Jetzt wird nicht getippt")
        player = self.players.get(pid)
        if player is None:
            raise GameError("Spieler unbekannt")
        if self.current is None:
            raise GameError("Keine Karte in dieser Runde")
        if not 0 <= slot <= len(player.timeline):
            raise GameError(f"Slot {slot} gibt es nicht (0..{len(player.timeline)})")
        player.pending = int(slot)

    def reveal(self) -> list[dict]:
        if self.state != "playing" or self.current is None:
            raise GameError("Nichts aufzuloesen")
        card = self.current
        results = []
        for player in self.players.values():
            ok = player.pending is not None and slot_is_correct(player.timeline, player.pending, card)
            player.correct_last = ok
            if ok:
                player.timeline.insert(player.pending, card)
            results.append({
                "player_id": player.id,
                "name": player.name,
                "slot": player.pending,
                "correct": ok,
                "score": player.score,
            })
        self.results = sorted(results, key=lambda r: r["score"], reverse=True)
        self.log.append({
            "round": self.round,
            "key": card.key,
            "title": card.title,
            "artist": card.artist,
            "year": card.year,
            "correct": [r["name"] for r in results if r["correct"]],
        })
        self.state = "revealed"
        winners = [p for p in self.players.values() if p.score >= TARGET_CARDS]
        if winners:
            best = max(winners, key=lambda p: p.score)
            self._finish(best)
        return self.results

    def _finish(self, winner: Player | None = None) -> None:
        self.state = "finished"
        self.winner = winner.name if winner else None

    def skip(self) -> None:
        """Host verwirft die aktuelle Karte, z.B. wenn Deezer kein Preview liefert."""
        if self.current is None:
            raise GameError("Keine Karte zum Ueberspringen")
        self.log.append({"round": self.round, "key": self.current.key,
                         "title": self.current.title, "artist": self.current.artist,
                         "year": self.current.year, "skipped": True, "correct": []})
        self.current = None
        self.preview = None
        self.state = "revealed"
        self.results = []
        for player in self.players.values():
            player.pending = None
            player.correct_last = None

    def reset(self) -> None:
        self.state = "lobby"
        self.current = None
        self.preview = None
        self.round = 0
        self.results = []
        self.log = []
        self.winner = None
        for player in self.players.values():
            player.timeline = [self.seed]
            player.pending = None
            player.correct_last = None

    # ---------- Sicht je Client ----------

    def snapshot(self, pid: str | None = None, is_host: bool = False) -> dict:
        playing = self.state in ("ready", "playing", "revealed", "finished")
        show_card = is_host or self.state == "revealed"
        snap: dict = {
            "type": "state",
            "code": self.code,
            "state": self.state,
            "round": self.round,
            "target": TARGET_CARDS,
            "winner": self.winner,
            "deck_left": len(self.deck),
            "players": [
                {
                    "id": p.id,
                    "name": p.name,
                    "score": p.score,
                    "pending": p.pending is not None,
                    "connected": p.connected,
                }
                for p in self.players.values()
            ],
            "results": self.results if self.state in ("revealed", "finished") else [],
            "log": self.log,
            "playing": playing,
        }
        if show_card and self.current:
            revealed = self.state in ("revealed", "finished")
            entry = {"key": self.current.key, "revealed": revealed}
            if is_host:
                entry["preview"] = self.preview
            if revealed:
                # Titel/Jahr erst nach dem Aufloesen — der Host-Bildschirm haengt im Partyraum
                # oft sichtbar, und in der Runde davor waere das die Loesung.
                entry.update(title=self.current.title, artist=self.current.artist,
                             year=self.current.year)
            snap["current"] = entry
        if pid and pid in self.players:
            me = self.players[pid]
            snap["me"] = {
                "id": me.id,
                "name": me.name,
                "score": me.score,
                "pending": me.pending,
                "correct_last": me.correct_last,
                "timeline": [
                    {"key": c.key, "title": c.title, "artist": c.artist, "year": c.year}
                    for c in me.timeline
                ],
            }
        return snap
