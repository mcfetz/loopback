"""Regelwerk fuer Song-Timeline: Raeume, Karten, Platzierungen.

Reine Logik ohne Netz und ohne FastAPI, damit tests/test_game.py sie ohne Server pruefen kann.
Der Server (app/main.py) haelt nur die Raeume und schiebt Snapshots an die Clients.
"""

from __future__ import annotations

import random
import secrets
import time
from dataclasses import dataclass, field

TARGET_CARDS = 10   # Kompatibilitaetsname: Mindestgroesse des Kartenpools
SEED_CARDS = 1      # jeder startet mit einer offenen Karte
CODE_ALPHABET = "ABCDEFGHJKLMNPQRSTUVWXYZ23456789"   # ohne O/0/I/1 (Verwechslung am Telefon)

ROUND_OPTIONS = (5, 10, 15, 20)   # waehlbare Rundenzahl
DEFAULT_ROUNDS = 10               # danach ist das Spiel vorbei, egal wie getippt wurde

BASE_POINTS = 100      # Punkte fuer eine richtige Antwort ohne Zeitbonus
MAX_MULTIPLIER = 3.0   # direkt nach dem Start der Runde
FAST_SECONDS = 20.0    # danach bleibt es beim Grundwert (x1,0)


def speed_multiplier(elapsed: float | None) -> float:
    """Zeitbonus: x3,0 sofort, linear runter auf x1,0 nach FAST_SECONDS."""
    if elapsed is None:
        return 1.0
    if elapsed <= 0:
        return MAX_MULTIPLIER
    if elapsed >= FAST_SECONDS:
        return 1.0
    factor = 1 + (MAX_MULTIPLIER - 1) * (1 - elapsed / FAST_SECONDS)
    return round(factor, 1)



class GameError(Exception):
    """Regelverstoss; der Server schickt den Text als {"type": "error"} an den Client."""


@dataclass(frozen=True)
class Card:
    key: str
    title: str
    artist: str
    year: int
    deezer_id: str
    genres: tuple[str, ...] = ()


def filter_pool(cards: list[Card], genres: list[str] | None) -> list[Card]:
    """Karten, deren Genre mindestens einen der Filter trifft. Leerer Filter = alles."""
    wanted = {g.strip().lower() for g in (genres or []) if g and g.strip()}
    if not wanted:
        return list(cards)
    return [c for c in cards if wanted & {g.lower() for g in c.genres}]


@dataclass
class Player:
    id: str
    name: str
    timeline: list[Card] = field(default_factory=list)
    pending: int | None = None      # Slot, den der Spieler getippt hat
    correct_last: bool | None = None
    connected: bool = True
    points: int = 0
    guessed_at: float | None = None
    last_delta: int = 0
    last_multiplier: float = 1.0

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
    solo: bool = False       # Ein-Geraet-Modus: derselbe Client ist Host und Spieler
    filter_genres: list[str] = field(default_factory=list)
    filter_playlist: str = ""
    round_started: float | None = None
    max_rounds: int = DEFAULT_ROUNDS

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
        self.round_started = None
        for player in self.players.values():
            player.points = 0
            player.last_delta = 0
            player.last_multiplier = 1.0
            player.guessed_at = None

    def next_round(self) -> Card:
        if self.state not in ("lobby", "ready", "revealed"):
            raise GameError("Erst aufloesen, dann die naechste Karte")
        if self.state == "lobby":
            self.start()
        if not self.deck:
            # Deck leer: der Beste gewinnt, sonst bleibt das Spiel ohne Ergebnis stehen.
            best = max(self.players.values(), key=lambda p: p.score) if self.players else None
            self._finish(best)
            raise GameError("Kartenpool ist leer — Runde beendet")
        self.current = self.deck.pop()
        self.preview = None
        self.round += 1
        self.state = "playing"
        self.results = []
        self.round_started = time.time()
        for player in self.players.values():
            player.pending = None
            player.correct_last = None
            player.guessed_at = None
            player.last_delta = 0
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
        player.guessed_at = time.time()      # Aenderung kostet Zeit, also neuen Stempel setzen

    def unplace(self, pid: str) -> None:
        """Tipp zuruecknehmen (Cancel am Slot-Button)."""
        if self.state != "playing":
            raise GameError("Jetzt wird nicht getippt")
        player = self.players.get(pid)
        if player is None:
            raise GameError("Spieler unbekannt")
        player.pending = None
        player.guessed_at = None

    def reveal(self) -> list[dict]:
        if self.state != "playing" or self.current is None:
            raise GameError("Nichts aufzuloesen")
        card = self.current
        results = []
        for player in self.players.values():
            ok = player.pending is not None and slot_is_correct(player.timeline, player.pending, card)
            elapsed = None
            if player.guessed_at is not None and self.round_started is not None:
                elapsed = player.guessed_at - self.round_started
            multiplier = speed_multiplier(elapsed)
            delta = round(BASE_POINTS * multiplier) if ok else 0
            player.correct_last = ok
            player.last_multiplier = multiplier
            player.last_delta = delta
            player.points += delta
            if ok:
                player.timeline.insert(player.pending, card)
            results.append({
                "player_id": player.id,
                "name": player.name,
                "slot": player.pending,
                "correct": ok,
                "score": player.score,
                "points": player.points,
                "delta": delta,
                "multiplier": multiplier,
                "seconds": round(elapsed, 1) if elapsed is not None else None,
            })
        self.results = sorted(results, key=lambda r: (r["points"], r["score"]), reverse=True)
        self.log.append({
            "round": self.round,
            "key": card.key,
            "title": card.title,
            "artist": card.artist,
            "year": card.year,
            "correct": [r["name"] for r in results if r["correct"]],
        })
        self.state = "revealed"
        if self.round >= self.max_rounds:
            # Nach der eingestellten Rundenzahl ist Schluss (egal ob richtig oder falsch);
            # gewertet wird nach Punkten, Karten entscheiden bei Gleichstand.
            best = max(self.players.values(), key=lambda p: (p.points, p.score))
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
            player.guessed_at = None
            player.last_delta = 0

    def reset(self) -> None:
        self.state = "lobby"
        self.current = None
        self.preview = None
        self.round = 0
        self.results = []
        self.log = []
        self.winner = None
        self.round_started = None
        for player in self.players.values():
            player.timeline = [self.seed]
            player.pending = None
            player.correct_last = None
            player.points = 0
            player.guessed_at = None
            player.last_delta = 0
            player.last_multiplier = 1.0

    # ---------- Sicht je Client ----------

    def snapshot(self, pid: str | None = None, is_host: bool = False) -> dict:
        playing = self.state in ("ready", "playing", "revealed", "finished")
        show_card = is_host or self.state == "revealed"
        snap: dict = {
            "type": "state",
            "code": self.code,
            "state": self.state,
            "round": self.round,
            "target": self.max_rounds,
            "winner": self.winner,
            "deck_left": len(self.deck),
            "genres": self.filter_genres,
            "playlist": self.filter_playlist,
            "round_started": self.round_started,
            "now": time.time(),
            "base_points": BASE_POINTS,
            "fast_seconds": FAST_SECONDS,
            "max_multiplier": MAX_MULTIPLIER,
            "players": [
                {
                    "id": p.id,
                    "name": p.name,
                    "score": p.score,
                    "points": p.points,
                    "delta": p.last_delta,
                    "multiplier": p.last_multiplier,
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
            elapsed_now = None
            if self.state == "playing" and self.round_started:
                elapsed_now = time.time() - self.round_started
            snap["me"] = {
                "id": me.id,
                "name": me.name,
                "score": me.score,
                "points": me.points,
                "delta": me.last_delta,
                "multiplier": me.last_multiplier,
                "multiplier_now": speed_multiplier(elapsed_now),
                "pending": me.pending,
                "correct_last": me.correct_last,
                "timeline": [
                    {"key": c.key, "title": c.title, "artist": c.artist, "year": c.year}
                    for c in me.timeline
                ],
            }
        return snap
