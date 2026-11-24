"""Live mouse-following mode with latest-target-wins flow control.

The browser streams pointer positions (machine coordinates) at ~30 Hz.
Queueing every point would accumulate lag behind the arm, so only the most
recent target is kept: a loop sends G01 to the current target, awaits the
robot's ok, and repeats — intermediate points are simply dropped when the
arm is slower than the mouse.
"""

from __future__ import annotations

import asyncio
from typing import Awaitable, Callable

from . import geometry
from .lineus import LineUsClient, LineUsError

Broadcast = Callable[[dict], Awaitable[None]]

LIVE_SPEED_DEFAULT = 10  # G94 S while in live mode (faster than default 5)


class LiveDraw:
    def __init__(self, broadcast: Broadcast) -> None:
        self._broadcast = broadcast
        self.active = False
        self._target: tuple[float, float] | None = None
        self._pen_down = False
        self._wakeup = asyncio.Event()
        self._task: asyncio.Task | None = None

    async def start(self, client: LineUsClient, speed: int = LIVE_SPEED_DEFAULT) -> None:
        if self.active:
            return
        await client.set_speed(s=speed, p=15)
        await client.pen_up()
        self.active = True
        self._target = None
        self._pen_down = False
        self._task = asyncio.create_task(self._run(client))

    async def stop(self, client: LineUsClient) -> None:
        if not self.active:
            return
        self.active = False
        self._wakeup.set()
        if self._task is not None:
            try:
                await self._task
            finally:
                self._task = None
        try:
            await client.pen_up()
            await client.set_speed(s=5, p=15)
        except LineUsError:
            pass

    def update(self, x: float, y: float, pen: bool) -> None:
        """Called for every pointer event; only the newest target survives."""
        if not self.active:
            return
        self._target = geometry.clip_point(x, y)
        self._pen_down = pen
        self._wakeup.set()

    async def set_speed(self, client: LineUsClient, speed: int) -> None:
        await client.set_speed(s=speed, p=15)

    async def _run(self, client: LineUsClient) -> None:
        sent_pos: tuple[float, float] | None = None
        sent_pen = False
        try:
            while self.active:
                target, pen = self._target, self._pen_down
                if target is None or (target == sent_pos and pen == sent_pen):
                    self._wakeup.clear()
                    # Re-check after clearing: update() may have raced us.
                    if self._target == sent_pos and self._pen_down == sent_pen:
                        await self._wakeup.wait()
                    continue
                if pen != sent_pen:
                    # Lift before travelling to a new stroke start.
                    if pen and sent_pos != target:
                        await client.pen_up()
                        await client.g01(x=target[0], y=target[1])
                    await (client.pen_down() if pen else client.pen_up())
                    sent_pen = pen
                    sent_pos = target
                    continue
                await client.g01(x=target[0], y=target[1])
                sent_pos = target
        except LineUsError as exc:
            self.active = False
            await self._broadcast({"type": "log", "level": "error",
                                   "message": f"Modo live interrompido: {exc}"})
