"""Shared application state: robot client, websocket hub, job runner, live mode."""

from __future__ import annotations

import asyncio
import json
import os

from fastapi import WebSocket

from .jobs import JobRunner
from .lineus import LineUsClient, SimLineUs
from .livedraw import LiveDraw

DEFAULT_HOST = os.environ.get("LINEUS_HOST", "line-us.local")
FORCE_SIM = os.environ.get("LINEUS_SIM", "") not in ("", "0", "false")

POSITION_HZ = 10  # broadcast arm position to clients at this rate


class AppState:
    def __init__(self) -> None:
        self.client: LineUsClient = SimLineUs() if FORCE_SIM else LineUsClient()
        self.sockets: set[WebSocket] = set()
        self.jobs = JobRunner(self.broadcast)
        self.live = LiveDraw(self.broadcast)
        self._position_task: asyncio.Task | None = None
        self._last_sent_position: tuple[float, float, float] | None = None

    # Websocket hub ----------------------------------------------------------

    async def broadcast(self, payload: dict) -> None:
        message = json.dumps(payload)
        dead = []
        for ws in self.sockets:
            try:
                await ws.send_text(message)
            except Exception:
                dead.append(ws)
        for ws in dead:
            self.sockets.discard(ws)

    def add_socket(self, ws: WebSocket) -> None:
        self.sockets.add(ws)
        if self._position_task is None or self._position_task.done():
            self._position_task = asyncio.create_task(self._position_loop())

    def remove_socket(self, ws: WebSocket) -> None:
        self.sockets.discard(ws)

    # Robot ------------------------------------------------------------------

    async def set_simulation(self, sim: bool) -> None:
        if self.client.is_simulator == sim:
            return
        if self.jobs.busy:
            raise RuntimeError("Pára o trabalho em curso antes de mudar de modo")
        await self.live.stop(self.client)
        await self.client.disconnect()
        self.client = SimLineUs() if sim else LineUsClient()

    def status_payload(self) -> dict:
        return {
            "type": "status",
            "connected": self.client.connected,
            "simulator": self.client.is_simulator,
            "host": self.client.host,
            "hello": self.client.hello,
            "default_host": DEFAULT_HOST,
            "live": self.live.active,
            "job": self.jobs.status_payload(),
        }

    async def _position_loop(self) -> None:
        while self.sockets:
            if self.client.connected:
                pos = self.client.position
                if pos != self._last_sent_position:
                    self._last_sent_position = pos
                    await self.broadcast({
                        "type": "position",
                        "x": round(pos[0], 2),
                        "y": round(pos[1], 2),
                        "z": round(pos[2], 2),
                    })
            await asyncio.sleep(1 / POSITION_HZ)
        self._position_task = None


state = AppState()
