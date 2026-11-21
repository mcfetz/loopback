"""Spielt eine Solo-Partie korrekt zu Ende und prueft, ob die Highscore-Liste sie aufnimmt.

Die richtige Position wird aus data/pool.json berechnet (der Server schickt vor dem Auflösen
nur den Karten-Schluessel, nicht das Jahr).

Aufruf: venv/bin/python scripts/check_highscores.py [base-url]
"""

from __future__ import annotations

import asyncio
import json
import sys
import urllib.request
from pathlib import Path

import websockets

BASE = sys.argv[1] if len(sys.argv) > 1 else "http://127.0.0.1:8123"
WS = BASE.replace("http", "ws") + "/ws"
POOL = Path(__file__).resolve().parent.parent / "data" / "pool.json"


def years() -> dict[str, int]:
    return {c["key"]: int(c["year"]) for c in json.loads(POOL.read_text())["cards"]}


async def recv(ws, pred, limit: int = 60) -> dict:
    for _ in range(limit):
        msg = json.loads(await asyncio.wait_for(ws.recv(), 20))
        if pred(msg):
            return msg
    raise AssertionError("kein passendes Paket erhalten")


def is_state(name: str):
    return lambda m: m.get("type") == "state" and m.get("state") == name


async def main() -> int:
    known = years()
    sock = await websockets.connect(WS)
    await sock.send(json.dumps({"action": "solo", "name": "Highscore-Test", "rounds": 5}))
    snap = await recv(sock, lambda m: m["type"] == "state")

    for _ in range(5):
        await sock.send(json.dumps({"action": "next"}))
        playing = await recv(sock, is_state("playing"))
        year = known[playing["current"]["key"]]
        timeline = playing["me"]["timeline"]
        slot = sum(1 for c in timeline if c["year"] <= year)
        await sock.send(json.dumps({"action": "place", "slot": slot}))
        await recv(sock, lambda m: m["type"] == "state" and m.get("me")
                  and m["me"]["pending"] == slot)
        await sock.send(json.dumps({"action": "reveal"}))
        snap = await recv(sock, lambda m: m["type"] == "state"
                          and m["state"] in ("revealed", "finished"))

    assert snap["state"] == "finished", snap["state"]
    me = snap["me"]
    print(f"1) Partie beendet: {me['points']} Punkte, {me['score']} Karten, "
          f"{snap['round']} Runden")

    with urllib.request.urlopen(f"{BASE}/api/highscores?limit=10", timeout=10) as resp:
        rows = json.loads(resp.read())["scores"]
    names = [r["name"] for r in rows]
    print(f"2) Highscore ({len(rows)} Eintraege): "
          + ", ".join(f"{r['name']} {r['points']}P/{r['rounds']}R" for r in rows))
    assert "Highscore-Test" in names, names
    entry = next(r for r in rows if r["name"] == "Highscore-Test")
    assert entry["points"] == me["points"], (entry, me)
    assert entry["cards"] == me["score"] - 1
    assert entry["solo"] is True
    order = [r["points"] for r in rows]
    assert order == sorted(order, reverse=True), order
    print("3) Eintrag korrekt, Liste absteigend sortiert")
    print("\nHIGHSCORE-CHECK BESTANDEN")
    return 0


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))
