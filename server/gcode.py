"""Parse uploaded G-code into pen-down polylines in machine coordinates.

Supported: G0/G00/G1/G01 with X/Y/Z (modal Z decides pen up/down, threshold
Z < 500 = down, matching community libraries). Everything else is skipped
with a warning so the user knows what was ignored.
"""

from __future__ import annotations

from dataclasses import dataclass, field

from . import geometry
from .lineus import parse_gcode_params

MAX_WARNINGS = 20
PEN_DOWN_THRESHOLD = 500.0


@dataclass
class ParseResult:
    polylines: list[geometry.Polyline] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)
    out_of_bounds: int = 0
    lines_total: int = 0
    moves: int = 0


def _warn(result: ParseResult, message: str) -> None:
    if len(result.warnings) < MAX_WARNINGS:
        result.warnings.append(message)
    elif len(result.warnings) == MAX_WARNINGS:
        result.warnings.append("… (mais avisos omitidos)")


def parse(text: str) -> ParseResult:
    result = ParseResult()
    x, y, z = geometry.HOME
    current: geometry.Polyline = []

    def end_path() -> None:
        nonlocal current
        if len(current) >= 2:
            result.polylines.append(current)
        current = []

    for lineno, raw in enumerate(text.splitlines(), start=1):
        line = raw.split(";", 1)[0].split("(", 1)[0].strip()
        if not line:
            continue
        result.lines_total += 1
        cmd = line.split()[0].upper()
        if cmd in ("G0", "G00", "G1", "G01"):
            params = parse_gcode_params(line)
            new_x = params.get("X", x)
            new_y = params.get("Y", y)
            new_z = params.get("Z", z)
            pen_was_down = z < PEN_DOWN_THRESHOLD
            pen_is_down = new_z < PEN_DOWN_THRESHOLD
            moved_xy = (new_x, new_y) != (x, y)
            if moved_xy and not geometry.in_bounds(new_x, new_y):
                result.out_of_bounds += 1
            if pen_is_down and not pen_was_down:
                end_path()
                current = [(x, y)]
            if pen_is_down and moved_xy:
                if not current:
                    current = [(x, y)]
                current.append((new_x, new_y))
            if pen_was_down and not pen_is_down:
                end_path()
            x, y, z = new_x, new_y, new_z
            result.moves += 1
        elif cmd == "G28":
            end_path()
            x, y, z = geometry.HOME
        elif cmd in ("G94", "G54", "G40", "G31") or cmd.startswith("M"):
            _warn(result, f"linha {lineno}: '{cmd}' ignorado (não suportado no preview)")
        else:
            _warn(result, f"linha {lineno}: comando desconhecido '{cmd}' ignorado")

    end_path()
    if result.out_of_bounds:
        result.warnings.insert(
            0,
            f"{result.out_of_bounds} movimento(s) fora da área segura "
            f"(X {geometry.X_MIN:.0f}–{geometry.X_MAX:.0f}, "
            f"Y {geometry.Y_MIN:.0f}–{geometry.Y_MAX:.0f}) — "
            "usa a opção 'Ajustar à área' ou corrige o ficheiro.",
        )
    if not result.polylines:
        result.warnings.append(
            "Nenhum traço pen-down encontrado (G01 com Z<500 seguido de movimentos XY)."
        )
    return result
