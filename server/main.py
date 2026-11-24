"""FastAPI app: static UI, REST API for uploads/jobs, websocket for realtime.

Run with:  uvicorn server.main:app --host 0.0.0.0 --port 8000
"""

from __future__ import annotations

import json
from pathlib import Path

from fastapi import FastAPI, File, Form, HTTPException, UploadFile, WebSocket, WebSocketDisconnect
from fastapi.responses import FileResponse, JSONResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel

from . import gcode, image2lines
from .lineus import LineUsError
from .state import state

WEB_DIR = Path(__file__).resolve().parent.parent / "web"

app = FastAPI(title="Line-us Web UI")


@app.get("/")
async def index() -> FileResponse:
    return FileResponse(WEB_DIR / "index.html")


# Connection ------------------------------------------------------------------


class ConnectRequest(BaseModel):
    host: str = ""
    sim: bool = False


@app.post("/api/connect")
async def connect(req: ConnectRequest) -> JSONResponse:
    try:
        await state.set_simulation(req.sim)
        host = req.host.strip() or state.status_payload()["default_host"]
        hello = await state.client.connect(host)
    except (LineUsError, RuntimeError) as exc:
        raise HTTPException(status_code=502, detail=str(exc)) from exc
    await state.broadcast(state.status_payload())
    return JSONResponse({"ok": True, "hello": hello})


@app.post("/api/disconnect")
async def disconnect() -> JSONResponse:
    await state.live.stop(state.client)
    await state.jobs.stop()
    await state.client.disconnect()
    await state.broadcast(state.status_payload())
    return JSONResponse({"ok": True})


@app.get("/api/status")
async def status() -> JSONResponse:
    return JSONResponse(state.status_payload())


# Uploads / conversions ---------------------------------------------------------


@app.post("/api/gcode")
async def upload_gcode(file: UploadFile = File(...)) -> JSONResponse:
    raw = await file.read()
    if len(raw) > 8 * 1024 * 1024:
        raise HTTPException(status_code=413, detail="Ficheiro demasiado grande (máx. 8 MB)")
    try:
        text = raw.decode("utf-8", errors="replace")
    except Exception as exc:  # pragma: no cover - decode with replace can't fail
        raise HTTPException(status_code=400, detail="Ficheiro ilegível") from exc
    result = gcode.parse(text)
    job = state.jobs.register(
        name=file.filename or "gcode",
        polylines=result.polylines,
        warnings=result.warnings,
        source="gcode",
    )
    payload = job.preview_payload()
    payload["out_of_bounds"] = result.out_of_bounds
    return JSONResponse(payload)


@app.post("/api/image")
async def upload_image(
    file: UploadFile = File(...),
    detail: int = Form(50),
    simplify: float = Form(1.0),
    hatching: bool = Form(False),
    hatch_density: int = Form(50),
    invert: bool = Form(False),
) -> JSONResponse:
    raw = await file.read()
    if len(raw) > 16 * 1024 * 1024:
        raise HTTPException(status_code=413, detail="Imagem demasiado grande (máx. 16 MB)")
    params = image2lines.ImageParams(
        detail=detail,
        simplify=simplify,
        hatching=hatching,
        hatch_density=hatch_density,
        invert=invert,
    )
    try:
        polylines, warnings = image2lines.convert(raw, params)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    job = state.jobs.register(
        name=file.filename or "imagem",
        polylines=polylines,
        warnings=warnings,
        source="image",
    )
    return JSONResponse(job.preview_payload())


# Job control -------------------------------------------------------------------


class JobStartRequest(BaseModel):
    job_id: str
    fit: bool = False


@app.post("/api/job/start")
async def job_start(req: JobStartRequest) -> JSONResponse:
    if not state.client.connected:
        raise HTTPException(status_code=409, detail="Liga-te ao robô primeiro")
    if state.live.active:
        raise HTTPException(status_code=409, detail="Sai do modo live primeiro")
    try:
        await state.jobs.start(req.job_id, state.client, fit=req.fit)
    except (RuntimeError, KeyError) as exc:
        detail = exc.args[0] if exc.args else str(exc)
        raise HTTPException(status_code=409, detail=detail) from exc
    return JSONResponse(state.jobs.status_payload())


@app.post("/api/job/pause")
async def job_pause() -> JSONResponse:
    await state.jobs.pause(state.client)
    return JSONResponse(state.jobs.status_payload())


@app.post("/api/job/resume")
async def job_resume() -> JSONResponse:
    state.jobs.resume()
    await state.broadcast(state.jobs.status_payload())
    return JSONResponse(state.jobs.status_payload())


@app.post("/api/job/stop")
async def job_stop() -> JSONResponse:
    await state.jobs.stop()
    return JSONResponse(state.jobs.status_payload())


# Websocket ---------------------------------------------------------------------


@app.websocket("/ws")
async def websocket_endpoint(ws: WebSocket) -> None:
    await ws.accept()
    state.add_socket(ws)
    await ws.send_text(json.dumps(state.status_payload()))
    try:
        while True:
            try:
                msg = json.loads(await ws.receive_text())
            except json.JSONDecodeError:
                continue
            await handle_ws_message(ws, msg)
    except WebSocketDisconnect:
        pass
    finally:
        state.remove_socket(ws)


async def handle_ws_message(ws: WebSocket, msg: dict) -> None:
    kind = msg.get("type")
    try:
        if kind == "live":
            state.live.update(float(msg["x"]), float(msg["y"]), bool(msg["pen"]))
        elif kind == "live_start":
            if state.jobs.busy:
                raise RuntimeError("Há um trabalho em curso")
            await state.live.start(state.client, speed=int(msg.get("speed", 10)))
            await state.broadcast(state.status_payload())
        elif kind == "live_stop":
            await state.live.stop(state.client)
            await state.broadcast(state.status_payload())
        elif kind == "speed":
            await state.live.set_speed(state.client, int(msg.get("value", 10)))
        elif kind == "home":
            await state.client.home()
        elif kind == "pen_up":
            await state.client.pen_up()
        elif kind == "pen_down":
            await state.client.pen_down()
        elif kind == "gcode":
            # Manual single command from the UI console.
            response = await state.client.send(str(msg.get("command", "")).strip())
            await ws.send_text(json.dumps({"type": "log", "level": "info",
                                           "message": response}))
    except (LineUsError, RuntimeError, KeyError, ValueError) as exc:
        await ws.send_text(json.dumps({"type": "log", "level": "error",
                                       "message": str(exc)}))


app.mount("/static", StaticFiles(directory=WEB_DIR), name="static")
