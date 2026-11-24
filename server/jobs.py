"""JobRunner: stream a list of polylines to the robot as ok-gated G01 moves.

One job at a time. Pause lifts the pen and freezes the cursor; resume
returns to the exact segment where it stopped. Progress is broadcast over
the shared websocket hub.
"""

from __future__ import annotations

import asyncio
import enum
import uuid
from dataclasses import dataclass, field
from typing import Awaitable, Callable

from . import geometry
from .lineus import LineUsClient, LineUsError

Broadcast = Callable[[dict], Awaitable[None]]


class JobState(str, enum.Enum):
    IDLE = "idle"
    RUNNING = "running"
    PAUSED = "paused"
    STOPPED = "stopped"
    DONE = "done"
    ERROR = "error"


@dataclass
class Job:
    id: str
    name: str
    polylines: list[geometry.Polyline]
    warnings: list[str] = field(default_factory=list)
    source: str = "gcode"  # "gcode" | "image"

    @property
    def segments_total(self) -> int:
        return geometry.total_segments(self.polylines)

    def preview_payload(self) -> dict:
        bb = geometry.bounds_of(self.polylines)
        return {
            "job_id": self.id,
            "name": self.name,
            "source": self.source,
            "polylines": [[[round(x, 2), round(y, 2)] for x, y in line] for line in self.polylines],
            "warnings": self.warnings,
            "bounds": bb,
            "paths": len(self.polylines),
            "segments": self.segments_total,
        }


class JobRunner:
    def __init__(self, broadcast: Broadcast) -> None:
        self._broadcast = broadcast
        self.jobs: dict[str, Job] = {}
        self.state = JobState.IDLE
        self.active_job: Job | None = None
        self.segments_done = 0
        self.error: str | None = None
        self._task: asyncio.Task | None = None
        self._resume_event = asyncio.Event()
        self._resume_event.set()
        self._stop_requested = False

    # Job registry ----------------------------------------------------------

    def register(self, name: str, polylines: list[geometry.Polyline],
                 warnings: list[str], source: str) -> Job:
        job = Job(id=uuid.uuid4().hex[:12], name=name,
                  polylines=polylines, warnings=warnings, source=source)
        self.jobs[job.id] = job
        # Keep only the most recent few previews around.
        while len(self.jobs) > 8:
            oldest = next(iter(self.jobs))
            if self.active_job is not None and oldest == self.active_job.id:
                break
            del self.jobs[oldest]
        return job

    @property
    def busy(self) -> bool:
        return self.state in (JobState.RUNNING, JobState.PAUSED)

    def status_payload(self) -> dict:
        return {
            "type": "progress",
            "state": self.state.value,
            "job_id": self.active_job.id if self.active_job else None,
            "name": self.active_job.name if self.active_job else None,
            "segments_done": self.segments_done,
            "segments_total": self.active_job.segments_total if self.active_job else 0,
            "error": self.error,
        }

    # Control ---------------------------------------------------------------

    async def start(self, job_id: str, client: LineUsClient, fit: bool = False) -> None:
        if self.busy:
            raise RuntimeError("Já existe um trabalho em curso")
        job = self.jobs.get(job_id)
        if job is None:
            raise KeyError("Trabalho desconhecido — volta a fazer upload")
        polylines = job.polylines
        if fit:
            polylines = geometry.fit_polylines_to_area(polylines)
        self.active_job = job
        self.segments_done = 0
        self.error = None
        self._stop_requested = False
        self._resume_event.set()
        self.state = JobState.RUNNING
        self._task = asyncio.create_task(self._run(client, polylines))

    async def pause(self, client: LineUsClient) -> None:
        if self.state == JobState.RUNNING:
            self.state = JobState.PAUSED
            self._resume_event.clear()
            await self._broadcast(self.status_payload())

    def resume(self) -> None:
        if self.state == JobState.PAUSED:
            self.state = JobState.RUNNING
            self._resume_event.set()

    async def stop(self) -> None:
        if self.busy:
            self._stop_requested = True
            self._resume_event.set()
            if self._task is not None:
                await self._task

    # Execution -------------------------------------------------------------

    async def _run(self, client: LineUsClient, polylines: list[geometry.Polyline]) -> None:
        notify_every = max(1, geometry.total_segments(polylines) // 200)
        try:
            await self._broadcast(self.status_payload())
            for line in polylines:
                if len(line) < 2:
                    continue
                if await self._should_stop(client):
                    return
                await client.pen_up()
                x0, y0 = geometry.clip_point(*line[0])
                await client.g01(x=x0, y=y0)
                await client.pen_down()
                for point in line[1:]:
                    if await self._should_stop(client):
                        return
                    x, y = geometry.clip_point(*point)
                    await client.g01(x=x, y=y)
                    self.segments_done += 1
                    if self.segments_done % notify_every == 0:
                        await self._broadcast(self.status_payload())
            await client.pen_up()
            await client.home()
            self.state = JobState.DONE
        except LineUsError as exc:
            self.state = JobState.ERROR
            self.error = str(exc)
        except Exception as exc:  # noqa: BLE001 - surface anything to the UI
            self.state = JobState.ERROR
            self.error = f"Erro inesperado: {exc}"
        finally:
            await self._broadcast(self.status_payload())

    async def _should_stop(self, client: LineUsClient) -> bool:
        """Handle stop/pause between moves. Returns True when stopping."""
        if self._stop_requested:
            await self._finish_stopped(client)
            return True
        if not self._resume_event.is_set():
            pen_was_down = client.position[2] < 500
            await client.pen_up()
            await self._resume_event.wait()
            if self._stop_requested:
                await self._finish_stopped(client)
                return True
            if pen_was_down:
                await client.pen_down()
            await self._broadcast(self.status_payload())
        return False

    async def _finish_stopped(self, client: LineUsClient) -> None:
        try:
            await client.pen_up()
            await client.home()
        except LineUsError:
            pass
        self.state = JobState.STOPPED
