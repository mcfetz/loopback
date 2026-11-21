"""CLI-Wrapper um app.importer — dieselbe Logik wie der Import im UI.

Früher steckte hier eine zweite Kopie der Regeln (Fassungsfilter, Jahresermittlung, Genres).
Die liegt jetzt in `app/catalog.py` und `app/importer.py`, damit UI und CLI nie auseinander
laufen.

Aufruf:
    venv/bin/python scripts/import_playlists.py 12204287271
    venv/bin/python scripts/import_playlists.py --limit 10 https://www.deezer.com/playlist/…
"""

from __future__ import annotations

import argparse
import asyncio
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from app import importer


async def run_one(raw: str, limit: int) -> None:
    pid = importer.parse_playlist_id(raw)
    if not pid:
        print(f"KEINE PLAYLIST-ID in {raw!r}")
        return
    job = importer.Job(id="cli", playlist_id=pid)
    task = asyncio.create_task(importer.import_playlist(pid, job, limit=limit))
    while not task.done():
        await asyncio.sleep(5)
        print(f"  {job.title or pid}: {job.processed}/{job.total} geprueft, "
              f"{len(importer.pool_cards())} Karten im Pool")
    await task
    print(f"  -> {job.state}: {job.message}")
    print(f"     {job.as_dict()}")


async def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("playlists", nargs="+", help="Playlist-IDs oder Deezer-Links")
    ap.add_argument("--limit", type=int, default=0, help="nur die ersten N brauchbaren Karten")
    args = ap.parse_args()
    for raw in args.playlists:
        await run_one(raw, args.limit)
    return 0


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))
