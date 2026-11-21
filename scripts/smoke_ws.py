"""End-to-End-Smoke: Host + zwei Spieler ueber echte WebSockets, echtes Deezer-Preview.

Voraussetzung: Server laeuft (venv/bin/uvicorn app.main:app --port 8123).
Aufruf: venv/bin/python scripts/smoke_ws.py [base-url]
"""

from __future__ import annotations

import asyncio
import json
import sys
import urllib.request

import websockets

BASE = sys.argv[1] if len(sys.argv) > 1 else "http://127.0.0.1:8123"
WS = BASE.replace("http", "ws") + "/ws"


async def recv(ws, pred, limit: int = 40) -> dict:
    for _ in range(limit):
        msg = json.loads(await asyncio.wait_for(ws.recv(), 15))
        if pred(msg):
            return msg
        if msg.get("type") == "error":
            raise AssertionError(f"Serverfehler: {msg['message']}")
    raise AssertionError("kein passendes Paket erhalten")


def is_state(state: str):
    return lambda m: m.get("type") == "state" and m.get("state") == state


async def main() -> int:
    host = await websockets.connect(WS)
    await host.send(json.dumps({"action": "create"}))
    joined = await recv(host, lambda m: m["type"] == "joined")
    code = joined["code"]
    print(f"1) Raum angelegt: {code}")

    players = {}
    for name in ("Anna", "Ben"):
        sock = await websockets.connect(WS)
        await sock.send(json.dumps({"action": "join", "code": code, "name": name}))
        j = await recv(sock, lambda m: m["type"] == "joined")
        players[name] = (sock, j["player_id"])
    print(f"2) Beigetreten: {', '.join(players)}")

    # Spieler darf nicht steuern
    await players["Anna"][0].send(json.dumps({"action": "next"}))
    err = await recv(players["Anna"][0], lambda m: m["type"] == "error")
    print(f"3) Spieler-Befehl abgelehnt: {err['message']}")

    await host.send(json.dumps({"action": "start"}))
    await recv(host, is_state("ready"))

    await host.send(json.dumps({"action": "next"}))
    hs = await recv(host, is_state("playing"))
    preview = hs["current"]["preview"]
    assert preview.startswith("http"), preview
    assert "year" not in hs["current"] and "title" not in hs["current"], hs["current"]
    print(f"4) Host: Runde {hs['round']}, {hs['deck_left']} Karten uebrig, Lösung nicht im Paket")

    ps = await recv(players["Anna"][0], is_state("playing"))
    assert "current" not in ps, ps.get("current")
    print("5) Spieler-Paket enthaelt weder Karte noch Audio")

    req = urllib.request.Request(preview, headers={"User-Agent": "smoke"})
    with urllib.request.urlopen(req, timeout=15) as resp:
        print(f"6) Preview abrufbar: HTTP {resp.status}, {resp.headers.get('content-type')}, "
              f"{int(resp.headers.get('content-length', 0)) // 1024} KB")

    # beide tippen (Anna 0, Ben ans Ende)
    await players["Anna"][0].send(json.dumps({"action": "place", "slot": 0}))
    anna_state = await recv(players["Anna"][0], lambda m: m["type"] == "state" and "me" in m)
    assert anna_state["me"]["pending"] == 0
    ben_slot = len((await recv(players["Ben"][0], lambda m: m["type"] == "state" and "me" in m))["me"]["timeline"])
    await players["Ben"][0].send(json.dumps({"action": "place", "slot": ben_slot}))
    print("7) Beide haben getippt")

    await host.send(json.dumps({"action": "reveal"}))
    hs = await recv(host, is_state("revealed"))
    results = hs["results"]
    assert len(results) == 2, results
    assert "year" in hs["current"] and "title" in hs["current"]
    print(f"8) Aufgeloest: {hs['current']['title']} — {hs['current']['artist']} ({hs['current']['year']})")
    for r in results:
        print(f"     {r['name']}: {'richtig' if r['correct'] else 'falsch'} -> {r['score']} Karten")

    ps = await recv(players["Ben"][0], is_state("revealed"))
    assert ps["me"]["timeline"], ps["me"]
    print(f"9) Spieler sieht jetzt Karte und Jahr; eigene Timeline {len(ps['me']['timeline'])} Karten")

    # zweite Runde, Skip-Pfad
    await host.send(json.dumps({"action": "next"}))
    await recv(host, is_state("playing"))
    await host.send(json.dumps({"action": "skip"}))
    await recv(host, is_state("revealed"))
    print("10) Karte uebersprungen, keine Punkte vergeben")

    for sock, _ in players.values():
        await sock.close()
    await host.close()
    print("\nSMOKE BESTANDEN")
    return 0


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))
