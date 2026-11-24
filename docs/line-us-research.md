# Line-us Robotic Drawing Arm: Technical Research for a Custom Python Control Project

## TL;DR
- **Building a custom Python controller for Line-us is very feasible and well-supported:** the robot exposes a documented, open G-code-like protocol over a raw TCP socket on port **1337** (and a websocket on port 80 since firmware 3.0.0), and there is an official, pip-installable Python module (`pip install lineus`) plus a mature community JavaScript library you can model your design on.
- **The hard constraints to design around are physical and geometric, not protocol:** a small non-rectangular SCARA work envelope (safe rectangle roughly X 800–1700, Y −900 to +900 machine units ≈ 9 cm × 4.5 cm), a single pen with only binary up/down Z (no pressure, no color, no tool change), low positional precision / a deliberately "wobbly" line, and a synchronous one-move-at-a-time command/response flow gated by WiFi latency.
- **Best architecture:** generate or import vector geometry (SVG/paths), flatten curves to short line segments, map your coordinate space onto Line-us machine coordinates (using `G54` scale/offset or your own transform), and stream `G01` moves, reading the `ok` response after each move to stay in sync.

## Key Findings

### 1. What Line-us is, and hardware
- Line-us is a small internet-connected robot drawing arm created in London by product/industrial designer **Durrell Bishop** and technologist **Robert Poll**. Per Dezeen and Core77, the concept "emerged during a chance meeting between Bishop and London based kinetic artist, Tim Lewis," whose mechanical sketch-producing artworks inspired it. It draws with a real pen on paper, mimicking on-screen strokes.
- The **Kickstarter campaign ran Feb 1 – Mar 3, 2017 (30 days) with a £39,000 funding goal**, offering backers the first 1,000 machines. Dezeen (25 May 2017) reported backers could buy Line-us "for £69 with an exclusive £49 to super early backers on Kickstarter, retail price will later be around £99."
- It is a **2-axis SCARA-style arm** driven by hobby servos through a four-bar linkage, plus a third servo for pen lift (Z). Early prototypes used 9g digital servos; the exact production servos were never publicly specified.
- The brain is an **Espressif ESP8266** microcontroller, which provides the WiFi and runs the firmware. The internal filesystem (SPIFFS) is ~1.5–2 MB and stores calibration plus saved drawings.
- **Power:** 5V via USB (the device does not ship with a battery/PSU); a USB power bank or any 5V USB supply works.
- **Body size:** roughly 83 × 97 × 25 mm (the box, excluding arm); about 150 g. A metal mounting plate with magnets in the base holds it on paper, a whiteboard or fridge.
- **Pen compatibility:** a sprung pen clamp/ring holds pens, pencils, sharpies, fineliners, even small paintbrushes — reported to fit barrels roughly 5–10 mm diameter. It is a single pen holder: no automatic pen change and no color change.
- **Status / availability:** still listed and orderable. The shop.line-us.com product page shows **"Regular price £98.00 GBP"** (metadata `og:price:amount 98.00 / currency GBP`) with an active "Add to cart" and "Copyright © 2026 Line-us," though stock quantity is not exposed.

### 2. Communication protocol and connectivity

**Connectivity / WiFi setup**
- Line-us is **2.4 GHz WiFi only** (a common gotcha — it will not see a 5 GHz-only SSID).
- Two modes: **AP/Setup mode** (the "Line-us-Setup" hotspot, LED flashes red) and **Station mode** (joined to your WiFi, solid blue).
- mDNS/Bonjour is supported; default hostname is **`line-us.local`**. The name is changeable via `M550` or the app.
- WiFi can be configured without the app: put the device in Setup mode, join `Line-us-Setup`, connect to `192.168.4.1:1337`, and send `M587 Smy-ssid P"my-password"`.

**Transport**
- **Raw TCP sockets on port 1337** — the original API. Test with `telnet line-us.local 1337`.
- **Websockets** (`ws://line-us.local`, unencrypted — the ESP8266 can't run SSL) — added in firmware 3.0.0. Same command set, except a few security-sensitive commands (e.g. `M587` WiFi config, `M171` factory reset) are disabled over websockets.
- On connect, the device sends a `hello` message with `KEY:value` pairs, e.g. `hello VERSION:"3.2.0 Nov 17 2019 17:54:57" NAME:line-us SERIAL:123456`.
- **Framing:** every message from Line-us is terminated with `\r\n\0`. On firmware <3.0.0 you *had* to read the full hello (including the `\0`) before sending; 3.0.0+ has a better TCP stack so this is no longer critical but is still advised. Commands sent over TCP must be terminated with one of/`\r`, `\n`, `\0`; websocket messages need no terminator.
- **Responses:** each command returns `ok` (success) or `error`, followed by optional `KEY:value` pairs, e.g. `ok X:1000.00 Y:0.00 Z:1000.00`.
- **Timing/flow control:** if you send a move while the arm is still moving, the command is accepted but the `ok` response is withheld until the prior move completes. This means reading the response after each command keeps your sender naturally in sync with the arm (at most one outstanding command).

**Command set (subset of RepRap G-code)** — from the official GCodeSpec:
- `G01 X Y Z` — **Linear interpolated move; the primary drawing command.** Can use a single axis, e.g. `G01 Z1000` to lift the pen without moving.
- `G00` — rapid move (each servo at full speed, not a straight line; can hit the body — rarely used).
- `G28` — home to (1000, 1000, 1000).
- `G94 S P` — "drawing speed": sets max step size (drawing units) of an interpolated move; default 5, range 1–30; `P` sets pen-up speed (default 15). Resets to default on restart.
- `G54 X Y S` — set scale and origin/offset for subsequent commands (scale applied first, then offset). Per-connection; resets to X0 Y0 S1 on each new connection.
- `G40` — uncompensated move (ignore `G54` scaling and calibration; `Kxy`/`Kz` to exclude axes).
- `G31` / `M374` — Z mapping (bed-leveling-like) and XY calibration.
- `M114` — get position (useful as heartbeat). `M115` — capabilities (`CONNECTMODE` ST/AP, `MACHINENAME`). `M122` — diagnostics.
- `M20`/`M28`/`M29`/`M30`/`M39` — list/save/end-save/delete/usage of drawings in onboard memory (files numbered 1–32; ~1.5 MB store).
- `M550` name, `M552` startup mode, `M587`/`M588` WiFi store/forget, `M997` firmware update, `M291` disable gestures.

**Coordinate system**
- Commands use **machine coordinates**. Origin (0,0) is at the centre of the servo shafts; **100 drawing units ≈ 5 mm** (1 unit ≈ 0.05 mm).
- **Home is (1000, 1000, 1000).** Z: **0 = pen fully down, 1000 = pen up**; community libraries treat Z<500 as "down."
- The work envelope is **not rectangular** — it is a curved/bent region. The largest safe inscribed rectangle (transcribed by a user, "heyarne," from the official drawing-area diagram in the forum thread "Drawing Area Shape") is about **X 800→1700, Y −900→+900** (≈ 90 mm × 45 mm). For Y<0 keep X ≥ 700 in software to avoid the pen screw fouling the body (older firmware 1.0.1 allowed X≥500 in that zone).
- If you command a point outside the reachable area, Line-us moves to the nearest reachable point and lifts the pen.

### 3. Existing open-source Python (and related) projects
- **Official Python module — `lineus`** (GitHub: Line-us/LineUsPythonModule; `pip install lineus`; docs on ReadTheDocs; GPL-3.0; latest tagged release 1.0.2). Python 3. Key API:
  - `LineUs()` — starts Bonjour discovery immediately on instantiation.
  - `.connect(name=None, wait=2, timeout=None)`, `.connected()`, `.disconnect()`.
  - `.g01(x, y, z)` — sends a move and waits for response; any of x/y/z optional.
  - `.get_hello_string()`, `.get_info()` (rich diagnostics dict incl. `ZMap`, `Cal`, `ServoReverse`), `.get_line_us_list()` (returns name, bonjour name, IP, port 1337), `.get_name()`.
  - `.send_gcode(gcode, parameters='')` and `.send_raw_gcode(gcode)` for arbitrary commands.
  - `.list_lineus_files()`, `.save_to_lineus(gcode, position)` (save to onboard memory slots 1–32).
  - `.ping(name, count=5)` — measures connection latency (returns mean/min/max/stdev in ms).
  - `.set_timeout(seconds)`.
  - It is intentionally simple/synchronous (blocks on each move's response). It has very few GitHub stars and limited recent maintenance, but it works and covers all GCodes.
- **Official simple examples** (Line-us/Line-us-Programming): `HelloWorld.py` (TCP) and `HelloWorldWebsockets.py`; also `lineusmusic` (`pip install lineusmusic`) which drives keyboards.
- **Community libraries in other languages** worth studying for design patterns:
  - **JavaScript — `@beardicus/line-us`** (Node + browser, websocket; the most thoughtfully engineered client). Notable features for a Python design: a **local command queue with configurable `concurrency`** (default 3) to buffer commands and hide latency; `moveTo`/`lineTo`/`penUp`/`penDown`/`home`; pause/resume/stop/clear; auto pen-lift on pause; events for state and live coordinates. It documents that pre-3.0.0 firmware had no buffer (send one at a time), and 3.0.0+ allows buffering multiple commands. It uses a canvas-style coordinate frame where {0,0} is the top-left of the "App drawing area" and machine home maps to {x:350, y:0}.
  - PHP (fxmorin/line-us), R (benjaminschwetz/rlineus, built on the Python lib), Node port (pandrr/line-us), C (Paul Haeberli), C#/Unity (NewChromantics/PopLineus), JS preview kit (funwithtriangles/line-us-js-kit), multi-machine coordinator in Processing (MarkusSelmke/MultipleLineUs).
- **SVG / image plotting tools:**
  - **Inkscape plugin** (Line-us/Inkscape-Plugin, by Anatoly Besplemennov) — draw directly from Inkscape; uses machine coordinates with no scaling; document units must be pixels. Community reports it broke on Inkscape 1.0+ and may need updating.
  - **SVG Plotter** (ixd-hof/LineUs_SVG, Michael Zöllner) — Processing 2.2.1 + Geomerative; Mac/Windows installers; a community fork (bareimage) updated it.
  - For your own pipeline, **vpype** (SVG optimization/linemerge/linesort/layout), **vsketch** (generative plotter framework on vpype), **hatched** (image→hatch SVG), and **linedraw** (image→vectorized line SVG) are the standard Python-ecosystem tools to feed geometry that you then stream to Line-us.

### 4. Capabilities and limitations
- **Precision / line quality:** deliberately a "drawer, not a plotter/printer" — Line-us produces a characteristic **wobbly line**, and reviewers note the drawings are "not perfect," which is part of its charm. Servo-driven SCARA geometry plus hobby servos means modest repeatability; expect visible wobble and some positional error, not CNC accuracy. Resolution is effectively governed by the ~0.05 mm/unit coordinate granularity and the `G94` step size, but mechanical accuracy is the real limit.
- **Line thickness:** fixed by the pen you fit — **no software line-weight control.** To vary apparent weight you must change pens or draw multiple passes/hatching. There is no Z-pressure control: Z is effectively binary up/down (with a calibratable height map).
- **Speed:** drawings are slow and a function of `G94` step size and path length; pen-up moves default faster (P=15) than pen-down (S=5).
- **Calibration / origin:** XY calibration (`M374`, move arm to calibration point first) and Z/"pen height" leveling (`G31` Z-map; the app's Pen Height menu) are needed for good results. Reviewers strongly advise using "Set Pen Height" before drawing. New machines ship with a default Z-map of `0;100;300;200`.
- **Onboard memory & autonomy:** drawings can be saved to the device (slots 1–32) and replayed via the button even offline; it can also "follow" a cloud sketchbook in blue mode.
- **Software ecosystem:** free official app (iOS, Android, macOS, Windows) for drawing/sharing; cloud sketchbooks; coding interfaces for Python, Processing, JavaScript, Scratch, Arduino, Raspberry Pi.
- **Firmware update:** via the app (`M997` triggers a download-and-restart). Forum reports show update friction (devices stuck connecting; needing to re-submit WiFi). Firmware 3.0.0 (Feb 2019) was a major release (websockets + better TCP stack); 3.2.0 builds exist.
- **Hackability:** the protocol is fully open and documented, so most "hacking" is at the API/app level rather than custom firmware. I found **no notable public custom-firmware or reverse-engineering project** for the ESP8266 firmware itself — the open API has largely removed the need. The ESP8266 platform is itself well-known and reflashable in principle, but the community effort has concentrated on clients/tools.

### 5. What to realistically expect from a custom Python project
**Feasible use cases**
- Plotting from SVG/vector art (flatten curves → segments → `G01` stream).
- Image-to-line/hatch plotting (via linedraw/hatched/vpype → your streamer).
- Generative art (compute paths in Python, stream live).
- Interactive/real-time control (mouse/tablet → moves), latency permitting.
- Automated repetitive drawing (signatures, invitations, patterns), repositioning the arm/paper to tile larger artwork.
- Saving drawings to onboard memory for offline replay.

**Hard constraints to design around**
- **Non-rectangular, small envelope:** clip/transform all geometry into the reachable region; treat the safe rectangle (≈X 800–1700, Y −900–900) as your default canvas and respect the X≥700-when-Y<0 rule.
- **Single pen, binary Z:** no color, no pen change, no line-weight or pressure; simulate tone only via hatching/cross-hatching/stippling and pen swaps.
- **Synchronous flow / latency:** the natural model is send-move → wait for `ok`. WiFi latency caps throughput; use a local queue with small concurrency (like the JS lib's default of 3) to smooth motion, but remember buffered commands must flush before a pause takes effect.
- **Coordinate mapping & calibration:** you own the transform from your design space to machine units; either use `G54` scale/offset or your own math. Calibrate XY and pen height first or output quality suffers.
- **Accuracy expectations:** design for a charming wobbly line, not precision CAD; avoid dense fine detail and tiny text.
- **Network requirements:** 2.4 GHz WiFi; discover via `line-us.local`/Bonjour or connect by IP:1337 (IP avoids name-lookup delays).

## Details
**Recommended protocol-handling design (Python):**
1. Discover via Bonjour or connect directly to `IP:1337`. Read and parse the `hello` line; store firmware version (branch behavior on <3.0.0 vs ≥3.0.0).
2. Maintain a TCP socket; send each command terminated with `\n`; read until `\0`/`\r\n\0`; parse `ok`/`error` + KEY:values.
3. Implement a move queue with bounded concurrency to overlap network round-trips, mirroring beardicus' approach; inject `penUp` on pause.
4. Provide `move_to`/`line_to`/`pen_up`/`pen_down`/`home` semantics on top of `G01`, plus a coordinate transform layer (your units → machine units, with clipping to the safe polygon).
5. Use `M114` as an occasional heartbeat; handle reconnection.

**Geometry pipeline:** import SVG → flatten Béziers/arcs to polylines (vpype/shapely) → linemerge + linesort to minimize pen-up travel → transform/scale to fit the safe envelope → stream as `G01` with appropriate `G94` step.

**Key source links:**
- Protocol/getting started: https://github.com/Line-us/Line-us-Programming/blob/master/README.md
- Full G-code spec: https://github.com/Line-us/Line-us-Programming/blob/master/Documentation/GCodeSpec.md
- Drawing-area diagram (PDF, graphic only): https://github.com/Line-us/Line-us-Programming/blob/master/Documentation/LineUsDrawingArea.pdf
- Official Python module + docs: https://github.com/Line-us/LineUsPythonModule and https://lineuspythonmodule.readthedocs.io/en/latest/
- Best-engineered client to copy (JS): https://github.com/beardicus/line-us
- Inkscape plugin: https://github.com/Line-us/Inkscape-Plugin ; SVG plotter: https://github.com/ixd-hof/LineUs_SVG
- Ecosystem tooling: https://github.com/abey79/vpype , https://github.com/plottertools/hatched , https://github.com/LingDong-/linedraw
- Community forum: https://forum.line-us.com

## Recommendations
1. **Start from the official `lineus` module to validate hardware and calibration** (connect, home, draw the quickstart shape), then decide whether to build your own client. Given your pen-plotter background, you will likely outgrow the synchronous official module quickly.
2. **Build a thin async/queued TCP client** (port 1337) modeled on `@beardicus/line-us`: bounded concurrency buffer, `ok`-gated flow control, pause/resume with auto pen-lift, and live-coordinate callbacks. Prefer TCP over websockets unless you specifically need browser interop; both share the command set.
3. **Implement a coordinate/clip layer first.** Hard-code the safe rectangle (X 800–1700, Y −900–900) and the X≥700/Y<0 rule, and make scale/offset configurable (or use `G54`). Add an out-of-bounds check rather than relying on the firmware's clamp-and-lift fallback.
4. **Reuse the existing plotter ecosystem rather than reinventing it:** feed SVGs through vpype (flatten/linemerge/linesort/scale) and use hatched/linedraw for image work; your job is then just the transform + stream. This directly leverages your prior image-to-GCode/editor experience.
5. **Calibrate XY and pen height up front** in every session, and expose a "set pen height" routine; otherwise line quality will be inconsistent.
6. **Benchmark latency** with a ping/`M114` round-trip on your network and tune queue concurrency and `G94` step size accordingly.

**Thresholds that change the plan:**
- If you need precise, fine-detail or text output → Line-us is the wrong tool; use a real pen plotter (AxiDraw class). Reassess if wobble/accuracy is unacceptable in test plots.
- If you need multi-color → plan manual pen-swap stops (pause queue, lift, swap, resume) or accept single-color; there is no automated path.
- If real-time interactivity stutters → increase queue concurrency, switch to connecting by IP, and ensure a clean 2.4 GHz link; if still poor, pre-buffer whole drawings.

## Caveats
- The exact production servo models and a manufacturer-published mm drawing-area spec were never released; the physical drawing-area dimensions here are **derived** from the unit scale (100 units ≈ 5 mm) and a user's transcription of the official diagram, not an official mm figure. Treat ≈9 × 4.5 cm as approximate.
- Drawing-area bounds (X 800–1700, Y −900–900) come from a community forum post quoting the official `LineUsDrawingArea.pdf`; the PDF is a graphic without extractable text, so confirm against the diagram for your firmware. The full curved reachable region is somewhat larger than this inscribed safe rectangle.
- The Inkscape plugin and the Processing SVG plotter may need updating for current Inkscape/Processing versions (community reports of breakage on Inkscape 1.0+).
- Firmware-update reliability has drawn forum complaints; budget time for WiFi/setup quirks. 2.4 GHz-only is a frequent connection pitfall.
- The official Python module's last release predates some firmware; verify behavior against your unit's firmware version (the 1.0.2 PyPI release date could not be independently confirmed).
- I found no active custom-firmware/reverse-engineering project; if deep firmware modification is a goal, expect to do original ESP8266 work.