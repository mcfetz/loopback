"""Prueft die neuen Regeln am laufenden Server: Rundenzahl, Tipp zuruecknehmen,
OK/Cancel-Semantik (Spieler bestaetigt nur, Host loest auf).

Aufruf: venv/bin/python scripts/check_rounds.py [base-url]
"""

from __future__ import annotations

import asyncio
import json
import sys

import websockets

BASE = sys.argv[1] if len(sys.argv) > 1 else "http://127.0.0.1:8123"
WS = BASE.replace("http", "ws") + "/ws"


async def recv(ws, pred, limit: int = 40) -> dict:
    for _ in range(limit):
        msg = json.loads(await asyncio.wait_for(ws.recv(), 15))
        if pred(msg):
            return msg
    raise AssertionError("kein passendes Paket erhalten")


def is_state(state):
    return lambda m: m.get("type") == "state" and m.get("state") == state


async def main() -> int:
    host = await websockets.connect(WS)
    await host.send(json.dumps({"action": "create", "name": "Daniel", "rounds": 5}))
    code = (await recv(host, lambda m: m["type"] == "joined"))["code"]
    snap = await recv(host, lambda m: m["type"] == "state")
    print(f"1) Raum {code} mit target={snap['target']} (erwartet 5)")
    assert snap["target"] == 5, snap["target"]

    player = await websockets.connect(WS)
    await player.send(json.dumps({"action": "join", "code": code, "name": "Anna"}))
    await recv(player, lambda m: m["type"] == "joined")

    await host.send(json.dumps({"action": "start"}))
    await recv(host, is_state("ready"))

    # Spieler darf nicht aufloesen
    await host.send(json.dumps({"action": "next"}))
    await recv(host, is_state("playing"))
    await player.send(json.dumps({"action": "reveal"}))
    err = await recv(player, lambda m: m["type"] == "error")
    print(f"2) Spieler-Aufloesen abgelehnt: {err['message']}")

    # Spieler tippt, nimmt zurueck, tippt wieder (Cancel am Slot)
    await player.send(json.dumps({"action": "place", "slot": 0}))
    st = await recv(player, lambda m: m["type"] == "state" and m.get("me"))
    assert st["me"]["pending"] == 0
    await player.send(json.dumps({"action": "clear"}))
    st = await recv(player, lambda m: m["type"] == "state" and m.get("me")
                    and m["me"]["pending"] is None)
    print("3) Tipp zurueckgenommen (pending wieder null)")
    await player.send(json.dumps({"action": "place", "slot": 1}))
    st = await recv(player, lambda m: m["type"] == "state" and m.get("me")
                    and m["me"]["pending"] == 1)
    print("4) Spieler bestaetigt per OK: pending=1, Runde laeuft weiter"
          f" (state={st['state']})")
    assert st["state"] == "playing"

    # Host tippt und loest auf (OK am Slot ersetzt den Aufloesen-Button)
    await host.send(json.dumps({"action": "place", "slot": 0}))
    await recv(host, lambda m: m["type"] == "state" and m.get("me") and m["me"]["pending"] == 0)
    await host.send(json.dumps({"action": "reveal"}))
    rev = await recv(host, is_state("revealed"))
    print("5) Host loest auf: " + ", ".join(
        f"{r['name']} {'richtig' if r['correct'] else 'falsch'} +{r['delta']}" for r in rev["results"]))

    # Restliche Runden bis zum Ende durchspielen (Tipp darf auch leer bleiben)
    rounds = rev["round"]
    while True:
        await host.send(json.dumps({"action": "next"}))
        msg = await recv(host, lambda m: m["type"] == "state" and m["state"] in ("playing", "finished"))
        if msg["state"] == "finished":
            print(f"6) Spiel nach {msg['round']} Runden beendet, Gewinner: {msg['winner']}")
            assert msg["round"] == 5, msg["round"]
            break
        rounds += 1
        await host.send(json.dumps({"action": "reveal"}))
        rev = await recv(host, lambda m: m["type"] == "state" and m["state"] in ("revealed", "finished"))
        assert rounds <= 5, "Spiel laeuft zu lange"
        if rev["state"] == "finished":
            print(f"6) Spiel nach {rev['round']} Runden beendet, Gewinner: {rev['winner']}")
            break

    # ungueltige Rundenzahl faellt auf 10 zurueck
    solo = await websockets.connect(WS)
    await solo.send(json.dumps({"action": "solo", "rounds": 7}))
    snap = await recv(solo, lambda m: m["type"] == "state")
    print(f"7) rounds=7 wird abgelehnt, target faellt zurueck auf {snap['target']}")
    assert snap["target"] == 10
    print("\nRUNDEN-CHECK BESTANDEN")
    return 0


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))
