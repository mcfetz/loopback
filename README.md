# Jahresringe

Musik-Partyspiel im Stil von Hitster, aber mit Deezer statt Spotify — und ohne dass jemand
ein Konto verbinden muss. Ein Gerät ist Host und spielt die 30-Sekunden-Ausschnitte, alle
anderen joinen per Session-Code vom Handy, tippen ihre Platzierung und sehen ihren Punktestand.

## Regeln

- Jeder startet mit einer offenen Karte in seiner Timeline (Jahr sichtbar) — auch der Host.
- Der Host spielt eine Karte ab. Alle tippen, wo sie in *ihre* Timeline gehört
  (davor / zwischen zwei Jahren / danach).
- Auflösen: Passt die Platzierung, wandert die Karte in die Timeline und gibt einen Punkt.
  Falsch getippt oder gar nicht getippt: Karte verfällt.
- Wer zuerst 10 Karten in der Timeline hat, gewinnt.

Der Host ist Moderator **und** Mitspieler: er hat eine eigene Timeline und tippt mit. Die
Lösung sieht er trotzdem erst beim Auflösen — der Rechner steht ja im Partyraum.

## Solo

„Solo spielen" auf der Startseite: gleiche Regeln auf einem Gerät, ohne Session-Code,
ohne Mitspieler. Man steuert selbst und tippt selbst, 10 Karten sind das Ziel.

## Starten

```bash
python -m venv venv && venv/bin/pip install -r requirements.txt
venv/bin/uvicorn app.main:app --host 0.0.0.0 --port 8123
```

Oder als Container: `docker compose up -d --build`, dann `http://<host>:8123` aufrufen.

Host: „Spiel erstellen" → Code ansagen. Spieler: `http://<host>:8123/?code=CODE` öffnen,
Namen eintragen, fertig.

## Kartenpool

`data/pool.json` enthält die Karten: Titel, Interpret, **kuratiertes Originaljahr**, Deezer-ID.
Das Jahr ist im Spiel die einzige Wahrheit, weil Deezer nur das Album-Release-Datum kennt
(„Dancing Queen" → 2005 wegen *ABBA Gold*, tatsächlich 1976).

Pool neu bauen bzw. IDs auflösen:

```bash
python scripts/build_pool.py
```

Das Skript sucht jede Karte bei Deezer, prüft, ob ein Preview existiert, und listet jede
Jahr-Abweichung zur Kontrolle auf. Die Trefferauswahl nimmt zuerst die Toptracks des
Interpreten und verwirft Instrumental-, Karaoke-, Live-, Remix- und Medley-Fassungen —
die normale Suche liefert bei bekannten Titeln sonst genau die („Don't Start Now": 25
Treffer, kein Original). Neue Karten also in `scripts/build_pool.py` in `CARDS`
eintragen (Titel, Interpret, Jahr) und das Skript laufen lassen.

## Technik

- `app/game.py` — Regelwerk (Räume, Timelines, Auswertung), ohne Netz testbar.
- `app/main.py` — FastAPI, WebSocket-Raum, ein Broadcast pro Änderung.
- `app/deezer.py` — Pool laden, Preview-URL frisch auflösen.
- `static/` — eine Seite, zwei Ansichten (Host / Spieler), kein Build-Schritt.
- `tests/test_game.py` — Regeltests (`python -m pytest tests -q`).

Audio geht nur an den Host: Spieler bekommen weder Preview-URL noch Titel oder Jahr, bevor
der Host auflöst. Der Host sieht ebenfalls erst beim Auflösen, was gespielt wurde.

Die Preview-Links von Deezer tragen ein hmac-Token mit rund 15 Minuten Laufzeit (gemessen
21.11.2026), deshalb wird pro Runde frisch aufgelöst. Audio wird nicht gespeichert — das
untersagen die Deezer-Guidelines ausdrücklich; für eine private Runde ist das Streamen des
30-Sekunden-Ausschnitts der vorgesehene Weg.
