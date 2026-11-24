# Line-us Studio

A modern web UI for the [Line-us](https://www.line-us.com/) drawing robot, with three drawing modes:

- **🖱 Live** — the arm follows your mouse on the canvas in real time (press and drag to draw, release to lift the pen)
- **⚙ G-code** — upload a G-code file, preview it on the canvas and draw it with a progress bar, pause/resume/stop
- **🖼 Image** — upload an image and have it converted to lines (Canny contours + optional hatching for shading), with live preview and adjustable parameters

It also includes a robot panel (home, pen up/down, manual G-code console, real-time position) and a **simulation mode** so you can use the UI without the hardware.

## Quick start

```bash
pip install -r requirements.txt
uvicorn server.main:app --host 0.0.0.0 --port 8000
```

Open `http://localhost:8000`.

## Connecting to the robot

- The robot must be on the same network (2.4 GHz WiFi). Enter its IP in the header, or use `line-us.local`.
- The default host can be set with the `LINEUS_HOST` environment variable.
- Communication is G-code over TCP on port 1337, with natural flow control (each command waits for the robot's `ok`).

### Simulation mode

Enable the **Simulation** checkbox in the header (or start the server with `LINEUS_SIM=1`) to use a virtual robot with simulated latency — useful for testing conversions and the UI without hardware.

## Technical notes

- Safe drawing area: X 800–1700, Y −900–+900 in machine units (100 units ≈ 5 mm, ~90×45 mm usable). Anything outside it is flagged on upload and can be auto-adjusted with the *Fit to area* option.
- Z axis: 0 = pen down, 1000 = pen up.
- Live mode uses a *latest-target-wins* strategy: only the most recent mouse position is sent to the robot, so latency never accumulates.
- G-code parser supports `G0`/`G1` (X/Y/Z) and `G28`. Other commands are ignored with a warning.
- See [`docs/line-us-research.md`](docs/line-us-research.md) for the protocol research behind this project.

## Project structure

```
server/   FastAPI backend (TCP→browser bridge, job queue, image conversion)
web/      static frontend (vanilla JS + canvas)
docs/     technical research
```

## License

[MIT](LICENSE)
