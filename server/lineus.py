"""Asyncio TCP client for the Line-us robot, plus an offline simulator.

Protocol: G-code-like commands over TCP port 1337. Commands are terminated
with "\n"; every response from the robot ends with "\r\n\0". On connect the
robot sends a hello line, e.g.:

    hello VERSION:"3.2.0 ..." NAME:line-us SERIAL:123456

Each command is answered with "ok ..." or "error ...". If a move is sent
while the arm is still moving, the response is withheld until the move
completes — so awaiting each response keeps the sender in sync with the arm.
"""

from __future__ import annotations

import asyncio
import math
import re
import time

from . import geometry

DEFAULT_PORT = 1337


def parse_kv(line: str) -> dict[str, str]:
    """Parse 'ok X:1000.00 Y:0.00' / 'hello NAME:line-us' style key:values."""
    return {
        k: v.strip('"')
        for k, v in re.findall(r'(\w+):("[^"]*"|\S+)', line)
    }


def parse_gcode_params(gcode: str) -> dict[str, float]:
    """Parse 'G01 X100.5 Y-200' style words into {'X': 100.5, 'Y': -200.0}."""
    return {
        k.upper(): float(v)
        for k, v in re.findall(r"([A-Za-z])(-?\d+\.?\d*)", gcode)
    }


class LineUsError(Exception):
    pass


class LineUsClient:
    """Serialized, ok-gated client. One in-flight command at a time."""

    is_simulator = False

    def __init__(self) -> None:
        self._reader: asyncio.StreamReader | None = None
        self._writer: asyncio.StreamWriter | None = None
        self._lock = asyncio.Lock()
        self.hello: dict[str, str] = {}
        self.host: str | None = None
        # Last commanded position; refreshed by responses carrying X/Y/Z.
        self.position: tuple[float, float, float] = geometry.HOME

    @property
    def connected(self) -> bool:
        return self._writer is not None and not self._writer.is_closing()

    async def connect(self, host: str, port: int = DEFAULT_PORT, timeout: float = 5.0) -> dict[str, str]:
        await self.disconnect()
        try:
            self._reader, self._writer = await asyncio.wait_for(
                asyncio.open_connection(host, port), timeout
            )
            hello_line = await asyncio.wait_for(self._read_message(), timeout)
        except (OSError, asyncio.TimeoutError) as exc:
            await self.disconnect()
            raise LineUsError(f"Não foi possível ligar a {host}:{port}: {exc}") from exc
        self.host = host
        self.hello = parse_kv(hello_line)
        self.position = geometry.HOME
        return self.hello

    async def disconnect(self) -> None:
        writer, self._writer, self._reader = self._writer, None, None
        self.host = None
        self.hello = {}
        if writer is not None:
            try:
                writer.close()
                await writer.wait_closed()
            except OSError:
                pass

    async def _read_message(self) -> str:
        assert self._reader is not None
        raw = await self._reader.readuntil(b"\x00")
        return raw.rstrip(b"\x00\r\n").decode("utf-8", errors="replace")

    async def send(self, gcode: str, timeout: float = 60.0) -> str:
        """Send one command and wait for its response ('ok ...'/'error ...').

        The long default timeout accommodates slow pen-down moves: the robot
        withholds the response until the move finishes.
        """
        if not self.connected:
            raise LineUsError("Não ligado ao robô")
        async with self._lock:
            assert self._writer is not None
            self._writer.write(gcode.encode("utf-8") + b"\n")
            try:
                await self._writer.drain()
                response = await asyncio.wait_for(self._read_message(), timeout)
            except (OSError, asyncio.TimeoutError, asyncio.IncompleteReadError) as exc:
                await self.disconnect()
                raise LineUsError(f"Ligação perdida: {exc}") from exc
        kv = parse_kv(response)
        if {"X", "Y", "Z"} <= kv.keys():
            try:
                self.position = (float(kv["X"]), float(kv["Y"]), float(kv["Z"]))
            except ValueError:
                pass
        if response.startswith("error"):
            raise LineUsError(f"Robô devolveu erro para '{gcode}': {response}")
        return response

    # Convenience moves -----------------------------------------------------

    async def g01(self, x: float | None = None, y: float | None = None, z: float | None = None) -> str:
        parts = ["G01"]
        if x is not None:
            parts.append(f"X{x:.2f}")
        if y is not None:
            parts.append(f"Y{y:.2f}")
        if z is not None:
            parts.append(f"Z{z:.2f}")
        resp = await self.send(" ".join(parts))
        px, py, pz = self.position
        self.position = (
            x if x is not None else px,
            y if y is not None else py,
            z if z is not None else pz,
        )
        return resp

    async def pen_up(self) -> str:
        return await self.g01(z=geometry.PEN_UP_Z)

    async def pen_down(self, z: float = geometry.PEN_DOWN_Z) -> str:
        return await self.g01(z=z)

    async def home(self) -> str:
        resp = await self.send("G28")
        self.position = geometry.HOME
        return resp

    async def set_speed(self, s: int | None = None, p: int | None = None) -> str:
        parts = ["G94"]
        if s is not None:
            parts.append(f"S{max(1, min(30, int(s)))}")
        if p is not None:
            parts.append(f"P{max(1, min(30, int(p)))}")
        return await self.send(" ".join(parts))

    async def get_position(self) -> tuple[float, float, float]:
        resp = await self.send("M114")
        kv = parse_kv(resp)
        try:
            self.position = (float(kv["X"]), float(kv["Y"]), float(kv["Z"]))
        except (KeyError, ValueError):
            pass
        return self.position


class SimLineUs(LineUsClient):
    """Drop-in simulator: same interface, latency proportional to distance.

    Lets the whole UI be exercised without reachable hardware.
    """

    is_simulator = True

    # Roughly mimics the robot: pen-down moves are slower than pen-up.
    BASE_DELAY = 0.03
    UNITS_PER_SECOND_DOWN = 2500.0
    UNITS_PER_SECOND_UP = 7000.0

    def __init__(self) -> None:
        super().__init__()
        self._sim_connected = False
        self._speed_factor = 1.0

    @property
    def connected(self) -> bool:
        return self._sim_connected

    async def connect(self, host: str, port: int = DEFAULT_PORT, timeout: float = 5.0) -> dict[str, str]:
        await asyncio.sleep(0.2)
        self._sim_connected = True
        self.host = host or "simulador"
        self.hello = {
            "VERSION": "sim-1.0",
            "NAME": "line-us-sim",
            "SERIAL": "000000",
        }
        self.position = geometry.HOME
        return self.hello

    async def disconnect(self) -> None:
        self._sim_connected = False
        self.host = None
        self.hello = {}

    async def send(self, gcode: str, timeout: float = 60.0) -> str:
        if not self._sim_connected:
            raise LineUsError("Não ligado ao robô (sim)")
        async with self._lock:
            gcode = gcode.strip()
            cmd = gcode.split()[0].upper() if gcode else ""
            if cmd in ("G01", "G1", "G00", "G0"):
                kv = parse_gcode_params(gcode)
                px, py, pz = self.position
                x = float(kv.get("X", px))
                y = float(kv.get("Y", py))
                z = float(kv.get("Z", pz))
                dist = math.hypot(x - px, y - py) + abs(z - pz) * 0.3
                speed = (
                    self.UNITS_PER_SECOND_DOWN
                    if min(pz, z) < 500
                    else self.UNITS_PER_SECOND_UP
                ) * self._speed_factor
                await asyncio.sleep(self.BASE_DELAY + dist / speed)
                self.position = (x, y, z)
                return f"ok X:{x:.2f} Y:{y:.2f} Z:{z:.2f}"
            if cmd == "G28":
                await asyncio.sleep(0.4)
                self.position = geometry.HOME
                return "ok X:1000.00 Y:1000.00 Z:1000.00"
            if cmd == "G94":
                kv = parse_gcode_params(gcode)
                s = float(kv.get("S", 5))
                self._speed_factor = max(0.2, s / 5.0)
                await asyncio.sleep(self.BASE_DELAY)
                return "ok"
            if cmd == "M114":
                x, y, z = self.position
                return f"ok X:{x:.2f} Y:{y:.2f} Z:{z:.2f}"
            await asyncio.sleep(self.BASE_DELAY)
            return "ok"
