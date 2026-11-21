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
    print("7) Beide Spieler haben getippt")

    # der Host raet mit: eigene Timeline, eigener Tipp
    host_state = await recv(host, lambda m: m["type"] == "state" and "me" in m)
    assert host_state["me"]["timeline"], "Host hat keine eigene Timeline"
    assert "year" not in host_state["current"], "Host darf die Loesung nicht vorher sehen"
    await host.send(json.dumps({"action": "place", "slot": 0}))
    host_state = await recv(host, lambda m: m["type"] == "state" and m.get("me", {}).get("pending") == 0)
    print(f"7b) Host tippt mit ({host_state['me']['score']} Karte in seiner Timeline, Lösung noch verdeckt)")

    await host.send(json.dumps({"action": "reveal"}))
    hs = await recv(host, is_state("revealed"))
    results = hs["results"]
    assert len(results) == 3, results
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

    # ---- Solo-Modus: ein Client, kein Code fuer Mitspieler
    solo = await websockets.connect(WS)
    await solo.send(json.dumps({"action": "solo", "name": "Solo"}))
    sj = await recv(solo, lambda m: m["type"] == "joined")
    assert sj["role"] == "solo", sj
    st = await recv(solo, lambda m: m["type"] == "state")
    assert st["state"] == "ready" and st["me"]["score"] == 1, st["state"]
    print(f"11) Solo-Runde offen (Startkarte {st['me']['timeline'][0]['year']})")

    await solo.send(json.dumps({"action": "next"}))
    st = await recv(solo, lambda m: m["type"] == "state" and m["state"] == "playing")
    assert st["current"]["preview"].startswith("http")
    assert "year" not in st["current"], "Solo darf die Loesung auch nicht vorher sehen"
    slot = len(st["me"]["timeline"])
    print(f"12) Solo: Runde {st['round']}, Audio da, {slot} Karte(n) in der Timeline")

    await solo.send(json.dumps({"action": "place", "slot": slot}))
    await recv(solo, lambda m: m["type"] == "state" and m.get("me", {}).get("pending") is not None)
    await solo.send(json.dumps({"action": "reveal"}))
    st = await recv(solo, lambda m: m["type"] == "state" and m["state"] == "revealed")
    assert "year" in st["current"]
    res = st["results"][0]
    print(f"13) Solo aufgeloest: {st['current']['title']} ({st['current']['year']}) — "
          f"{'richtig' if res['correct'] else 'falsch'}, {res['score']} Karten")

    intruder = await websockets.connect(WS)
    await intruder.send(json.dumps({"action": "join", "code": sj["code"], "name": "Rein"}))
    err = await recv(intruder, lambda m: m["type"] == "error")
    print(f"14) Beitritt zur Solo-Runde abgelehnt: {err['message']}")
    await intruder.close()
    await solo.close()

    for sock, _ in players.values():
        await sock.close()
    await host.close()
    print("\nSMOKE BESTANDEN")
    return 0


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))
