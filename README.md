# LoopBack

Musik-Partyspiel im Stil von Hitster, aber mit Deezer statt Spotify — und ohne dass jemand
ein Konto verbinden muss. Ein Gerät ist Host und spielt die 30-Sekunden-Ausschnitte, alle
anderen joinen per Session-Code vom Handy, tippen ihre Platzierung und sehen ihren Punktestand.

## Regeln

- Jeder startet mit einer offenen Karte in seiner Timeline (Jahr sichtbar) — auch der Host.
- Der Host spielt eine Karte ab. Alle tippen, wo sie in *ihre* Timeline gehört
  (davor / zwischen zwei Jahren / danach).
- Tippen: auf einen Jahres-Button tippen — der Button wird zum Paar **OK / ✕**. OK bestätigt
  den Tipp (beim Host und im Solo-Modus löst es die Runde direkt auf — einen separaten
  Auflösen-Button gibt es nicht mehr), ✕ nimmt ihn zurück.
- Auflösen: Passt die Platzierung, wandert die Karte in die Timeline.
  Falsch getippt oder gar nicht getippt: Karte verfällt.
- Das Spiel endet nach der eingestellten Rundenzahl — 5, 10, 15 oder 20, Vorgabe 10 — egal ob
  richtig oder falsch getippt wurde.

Die Timeline ist zugleich die Runde-Historie: richtig gelegte Karten stehen grün, die
Startkarte neutral, und danebengelegte Karten erscheinen rot an der Stelle, an die sie
gehört hätten („daneben", zählen nicht mit). Eine separate Verlaufsliste gibt es nicht.

**Punkte:** Jede richtige Antwort gibt 100 Punkte mal Zeitbonus. Der Bonus startet bei ×3,0
und sinkt linear auf ×1,0 nach 20 Sekunden — wer zuerst tippt, bekommt also bis zu 300 Punkte,
wer nach 20 Sekunden tippt nur noch 100. Der Stempel wird beim Tippen gesetzt, ein späteres
Umentscheiden kostet Zeit. Falsch oder gar nicht getippt: 0 Punkte. Gewinner ist, wer am Ende
die meisten Punkte hat (bei Gleichstand entscheiden die Karten).

Der Host ist Moderator **und** Mitspieler: er hat eine eigene Timeline und tippt mit. Die
Lösung sieht er trotzdem erst beim Auflösen — der Rechner steht ja im Partyraum.

## Solo

„Solo spielen" auf der Startseite (im Block **Spielen**, neben „Spiel erstellen"): dieselben
Einstellungen (Name, Genres, Runden), gleiche Regeln auf einem Gerät, ohne Session-Code,
ohne Mitspieler. Man steuert selbst und tippt selbst, 10 Karten sind das Ziel.

## Starten

```bash
cd ~/development/loopback
python -m venv venv && venv/bin/pip install -r requirements.txt
venv/bin/uvicorn app.main:app --host 0.0.0.0 --port 8123
# Tests: venv/bin/pip install -r requirements-dev.txt && venv/bin/python -m pytest tests -q
```

Oder als Container: `docker compose up -d --build`, dann `http://<host>:8123` aufrufen.

Host: „Spiel erstellen" → Code ansagen. Spieler: `http://<host>:8123/?code=CODE` öffnen,
Namen eintragen, fertig.

## Genres

Vor dem Start lassen sich Genres als Chips auswählen (nichts gewählt = alle). Der Filter gilt
für Host-Runden und Solo-Runden; Spieler übernehmen ihn einfach mit dem Beitritt. Reicht der
gefilterte Pool nicht für ein Spiel (mindestens 12 Karten), meldet der Server das und man
wählt mehr Genres.

Die Genres stammen aus den Album-Angaben von Deezer, werden aber gebündelt: Deezer liefert
18 Feinkategorien für 77 Karten (viele mit nur einem Titel), das Skript fasst sie zu Pop,
Rock, Hip-Hop, Electronic, Soul & Funk, Film & Games, Folk & Country, Reggae und Jazz & Blues
zusammen und ergänzt „Deutsch" anhand einer Interpretenliste. Die Tabellen stehen in
`app/catalog.py` (`GENRE_BUCKETS`, `DEUTSCH`), damit UI-Import und CLI dieselben Regeln nutzen.

## Quelle im UI wählen und importieren

Die Startseite hat oben einen Block **Quelle**: Chips für „Alle Karten", „Kuratiert" und jede
importierte Playlist (mit Kartenzahl). Die Auswahl gilt für Host- und Solo-Runden; der Host
sieht sie unter dem Session-Code („Quelle: Hitster · Genres: Rock"). Die Genre-Chips zählen
innerhalb der gewählten Quelle (`GET /api/genres?playlist=…`) — beim Wechsel der Quelle
aktualisieren sie sich, und Genres, die es dort nicht gibt, fliegen aus der Auswahl.

Import direkt im UI: Deezer-Link oder Playlist-ID ins Feld, „Importieren" — der Import läuft
als Hintergrund-Job im Server (`app/importer.py`), die Seite zeigt den Fortschritt
(geprüfte Tracks, übernommene Karten) und aktualisiert danach Quellen und Genres. Pro Track
fällt eine Apple-Music-Abfrage an, deshalb dauert eine 300-Track-Playlist rund fünf Minuten.

`data/playlists.json` merkt sich die Quellen (ID, Titel, Kartenzahl), `data/pool.json` trägt
pro Karte `playlist`. Ein erneuter Import derselben Playlist ersetzt genau deren Karten,
kuratierte und fremde Playlists bleiben unangetastet.

Statt Karten einzeln zu pflegen, lassen sich Deezer-Playlists importieren:

```bash
python scripts/import_playlists.py 12204287271            # Playlist-ID
python scripts/import_playlists.py https://link.deezer.com/s/351vBqcAj6kc4KMvb5TZS
python scripts/import_playlists.py --limit 20 12204287271  # erst mal 20 Karten testen
```

Das Skript holt die Tracks, wirft alles ohne Preview, ohne brauchbare Fassung (Instrumental,
Karaoke, Live, Remix, Medley) und zu kurze Stücke raus, zieht die Genres aus dem Deezer-Album
und bestimmt das **Jahr über die Apple-Music-Suche** (iTunes-API). Deezer kennt nur das
Album-Datum („ABBA Gold" → Dancing Queen 2005), Apple nennt das Erstveröffentlichungsdatum
(1976) — auch bei Compilations. Genommen wird das häufigste Jahr der passenden Treffer, weil
einzelne Apple-Einträge kaputte Daten haben (ein „Radio Edit" von Meat Loaf steht dort mit
1966, richtig ist 1977).

Kuratierte Karten in `data/pool.json` bleiben erhalten und gewinnen beim Jahr. Alles
Unsichere landet in `data/pool-review.txt` (Jahr weicht stark vom Deezer-Album ab, kein Genre,
kein Jahr gefunden).

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

## Ergebnis-Modal

Am Ende der ganzen Partie legt sich ein Modal über die Seite: Ergebnis der letzten Runde,
Karten und Punkte gesamt, Platz in der Partie, Platz in der ewigen Highscore und die Top 5.
Bei mehreren Spielern steht der Endstand darunter, der eigene Platz ist markiert. Geschlossen
wird über „Schließen", das ✕ oder einen Klick neben das Fenster — nach jeder einzelnen Runde
kommt bewusst kein Fenster.

## Highscores

Jede beendete Partie landet auf dem Server in `data/highscores.json` (per `.gitignore` aussen
vor, das ist Laufzeit). Pro Name bleibt die **beste** Partie stehen — Punkte, Karten,
Rundenzahl, solo und Zeitstempel; wer in einer Partie weder Punkte noch Karten geholt hat,
wird nicht eingetragen. Die Startseite zeigt die Top 10, nach dem Auflösen der letzten Runde
aktualisiert sie sich selbst und nennt den eigenen Platz. Lesen kann sie jeder über
`GET /api/highscores?limit=10`.

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
