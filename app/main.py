"""Song-Timeline: FastAPI-Server mit WebSocket-Raum.

Ein Geraet ist Host (spielt das Audio, steuert die Runden), alle anderen joinen mit dem
Session-Code, tippen ihre Platzierung und sehen ihren Punktestand.
"""

from __future__ import annotations

import logging
import secrets
from pathlib import Path

from fastapi import FastAPI, HTTPException, WebSocket, WebSocketDisconnect
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles

from app import catalog, deezer, importer
from app.game import Card, GameError, Player, Room, TARGET_CARDS, filter_pool, new_code

log = logging.getLogger("song-timeline")
logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")

BASE_DIR = Path(__file__).resolve().parent.parent
STATIC_DIR = BASE_DIR / "static"

app = FastAPI(title="Song-Timeline")
app.mount("/static", StaticFiles(directory=STATIC_DIR), name="static")

ROOMS: dict[str, Room] = {}
CONNS: dict[str, list[dict]] = {}       # code -> [{"ws","pid","host"}]


def pool_cards(genres: list[str] | None = None, playlist: str | None = None) -> list[Card]:
    items = deezer.load_pool()
    if playlist == "kuratiert":
        items = [i for i in items if not i.get("playlist")]
    elif playlist:
        items = [i for i in items if i.get("playlist") == playlist]
    cards = [Card(**{k: item[k] for k in ("key", "title", "artist", "year", "deezer_id")},
                  genres=tuple(item.get("genres") or []))
             for item in items]
    return filter_pool(cards, genres)


def wanted_genres(msg: dict) -> list[str]:
    raw = msg.get("genres") or []
    if isinstance(raw, str):
        raw = [raw]
    return [str(g)[:32] for g in raw][:8]


def room_pool(msg: dict) -> tuple[list[Card], list[str], str]:
    genres = wanted_genres(msg)
    playlist = str(msg.get("playlist") or "").strip()[:40]
    cards = pool_cards(genres, playlist or None)
    if len(cards) < TARGET_CARDS + 2:
        raise GameError(f"Zu wenige Karten fuer diese Auswahl ({len(cards)} statt "
                        f"{TARGET_CARDS + 2}) — bitte mehr Genres waehlen oder eine andere "
                        f"Playlist")
    return cards, genres, playlist


@app.get("/")
async def index() -> FileResponse:
    return FileResponse(STATIC_DIR / "index.html")


@app.get("/api/genres")
async def genres() -> list[dict]:
    """Verfuegbare Genres mit Kartenzahl, haeufigste zuerst."""
    return catalog.grouped_genres(importer.pool_cards())


@app.get("/api/playlists")
async def playlists() -> dict:
    """Gemerkte Playlists als Rundenquellen, plus die kuratierten Karten."""
    cards = importer.pool_cards()
    counts: dict[str, int] = {}
    for card in cards:
        pid = card.get("playlist") or ""
        counts[pid] = counts.get(pid, 0) + 1
    return {
        "total": len(cards),
        "curated": counts.get("", 0),
        "items": [{**item, "cards_in_pool": counts.get(item["id"], 0)}
                  for item in importer.load_playlists()],
    }


@app.post("/api/playlists")
async def import_playlist(payload: dict) -> dict:
    raw = str((payload or {}).get("url") or "")
    pid = importer.parse_playlist_id(raw)
    if not pid:
        raise HTTPException(status_code=400,
                            detail="Keine Playlist-ID gefunden — Deezer-Link oder ID einsetzen")
    job = await importer.import_in_background(pid)
    log.info("Import gestartet: Playlist %s (Job %s)", pid, job.id)
    return job.as_dict()


@app.get("/api/playlists/jobs/{job_id}")
async def import_job(job_id: str) -> dict:
    job = importer.JOBS.get(job_id)
    if job is None:
        raise HTTPException(status_code=404, detail="Job unbekannt")
    return job.as_dict()



@app.get("/api/health")
async def health() -> dict:
    return {"ok": True, "rooms": len(ROOMS), "card_pool": len(deezer.load_pool())}


# ---------------------------------------------------------------- Senden

async def send_state(code: str) -> None:
    room = ROOMS.get(code)
    if room is None:
        return
    dead = []
    for conn in CONNS.get(code, []):
        try:
            await conn["ws"].send_json(room.snapshot(pid=conn["pid"], is_host=conn["host"]))
        except Exception:                      # Verbindung weg -> aufraeumen
            dead.append(conn)
    for conn in dead:
        if conn in CONNS.get(code, []):
            CONNS[code].remove(conn)


async def send_to(sock: WebSocket, payload: dict) -> None:
    try:
        await sock.send_json(payload)
    except Exception:
        pass


# ---------------------------------------------------------------- WebSocket

@app.websocket("/ws")
async def ws_endpoint(sock: WebSocket) -> None:
    await sock.accept()
    code: str | None = None
    pid: str | None = None
    is_host = False
    try:
        while True:
            try:
                msg = await sock.receive_json()
            except WebSocketDisconnect:
                break
            except ValueError:
                await send_to(sock, {"type": "error", "message": "Ungueltiges JSON"})
                continue
            action = (msg or {}).get("action")
            try:
                if action == "create":
                    host_id = secrets.token_urlsafe(8)
                    cards, genres, playlist = room_pool(msg)
                    room = Room.create(cards, host_id=host_id,
                                       code=new_code(set(ROOMS)))
                    room.filter_genres = genres
                    room.filter_playlist = playlist
                    host_name = str(msg.get("name") or "Host").strip()[:24] or "Host"
                    room.add_player(host_id, host_name)     # Host raet mit
                    ROOMS[room.code] = room
                    CONNS[room.code] = []
                    code, pid, is_host = room.code, host_id, True
                    CONNS[code].append({"ws": sock, "pid": pid, "host": True})
                    await send_to(sock, {"type": "joined", "role": "host",
                                         "code": room.code, "player_id": pid,
                                         "name": host_name})
                    log.info("Raum %s angelegt (Pool %d Karten)", room.code, len(room.deck) + 1)
                    await send_state(room.code)

                elif action == "solo":
                    # Ein Geraet, eine Person: derselbe Client steuert und tippt, ohne Code.
                    solo_pid = secrets.token_urlsafe(8)
                    cards, genres, playlist = room_pool(msg)
                    room = Room.create(cards, host_id=solo_pid, code=new_code(set(ROOMS)))
                    room.filter_genres = genres
                    room.filter_playlist = playlist
                    room.solo = True
                    name = str(msg.get("name") or "Solo").strip()[:24] or "Solo"
                    room.add_player(solo_pid, name)
                    room.start()
                    ROOMS[room.code] = room
                    CONNS[room.code] = []
                    code, pid, is_host = room.code, solo_pid, True
                    CONNS[code].append({"ws": sock, "pid": pid, "host": True})
                    await send_to(sock, {"type": "joined", "role": "solo",
                                         "code": code, "player_id": pid, "name": name})
                    log.info("Solo-Runde %s gestartet", room.code)
                    await send_state(code)

                elif action == "join":
                    join_code = str(msg.get("code", "")).strip().upper()
                    room = ROOMS.get(join_code)
                    if room is None:
                        raise GameError("Session nicht gefunden")
                    if room.solo:
                        raise GameError("Das ist eine Solo-Runde")
                    existing = str(msg.get("player_id") or "")
                    if existing and existing == room.host_id:      # Host-Reconnect
                        code, pid, is_host = room.code, existing, True
                        CONNS.setdefault(code, []).append({"ws": sock, "pid": pid, "host": True})
                        await send_to(sock, {"type": "joined", "role": "host",
                                             "code": code, "player_id": pid})
                        await send_state(code)
                        continue
                    player: Player | None = room.reattach(existing) if existing else None
                    if player is None:
                        player = room.add_player(secrets.token_urlsafe(8), str(msg.get("name", "")))
                    code, pid, is_host = room.code, player.id, False
                    CONNS.setdefault(code, []).append({"ws": sock, "pid": pid, "host": False})
                    await send_to(sock, {"type": "joined", "role": "player",
                                         "code": code, "player_id": pid, "name": player.name})
                    await send_state(code)

                elif action in ("start", "next", "reveal", "reset", "skip"):
                    room = ROOMS.get(code or "")
                    if room is None or not is_host:
                        raise GameError("Nur der Host steuert das Spiel")
                    if action == "start":
                        room.start()
                    elif action == "next":
                        card = room.next_round()
                        room.preview = await deezer.resolve_preview(card.deezer_id)
                        if not room.preview:
                            await send_to(sock, {"type": "warning",
                                                 "message": f"Kein Preview fuer '{card.title}' — "
                                                            f"mit 'Karte ueberspringen' weiter"})
                    elif action == "reveal":
                        room.reveal()
                    elif action == "skip":
                        room.skip()
                    elif action == "reset":
                        room.reset()
                    await send_state(room.code)

                elif action == "place":
                    room = ROOMS.get(code or "")
                    if room is None or pid is None:
                        raise GameError("Erst einer Session beitreten")
                    room.place(pid, int(msg.get("slot", -1)))
                    await send_state(room.code)

                elif action == "ping":
                    await send_to(sock, {"type": "pong"})

                else:
                    await send_to(sock, {"type": "error", "message": f"Unbekannte Aktion: {action}"})

            except (GameError, ValueError, TypeError) as exc:
                await send_to(sock, {"type": "error", "message": str(exc)})

    except WebSocketDisconnect:
        pass
    finally:
        if code and code in CONNS:
            CONNS[code] = [c for c in CONNS[code] if c["ws"] is not sock]
            room = ROOMS.get(code)
            if room and pid in room.players:
                room.players[pid].connected = False
            await send_state(code)
