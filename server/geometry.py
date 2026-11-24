"""Safe drawing area, clipping, fitting and path ordering for Line-us.

Machine coordinates: 100 units ~= 5mm. The reachable region is curved; we
work inside the largest safe inscribed rectangle (per the official drawing
area diagram). Z: 0 = pen down, 1000 = pen up.
"""

from __future__ import annotations

import math

import cv2
import numpy as np

# Safe inscribed rectangle of the Line-us drawing area (machine units).
X_MIN = 800.0
X_MAX = 1700.0
Y_MIN = -900.0
Y_MAX = 900.0

# For Y < 0 the pen screw can foul the body when X < 700. The safe rectangle
# already satisfies this, but arbitrary uploaded G-code is validated too.
X_MIN_NEG_Y = 700.0

PEN_UP_Z = 1000.0
PEN_DOWN_Z = 0.0

HOME = (1000.0, 1000.0, 1000.0)

# "Paper space": the landscape canvas shown in the UI. x grows to the right
# (0..1800), y grows downward (0..900), top edge nearest the robot body.
# Machine X is distance from the body, machine Y runs along the paper width.
PAPER_W = Y_MAX - Y_MIN  # 1800
PAPER_H = X_MAX - X_MIN  # 900

Point = tuple[float, float]
Polyline = list[Point]


def paper_to_machine(px: float, py: float) -> Point:
    return (X_MIN + py, Y_MAX - px)


def in_bounds(x: float, y: float) -> bool:
    if not (X_MIN <= x <= X_MAX and Y_MIN <= y <= Y_MAX):
        return False
    if y < 0 and x < X_MIN_NEG_Y:
        return False
    return True


def clip_point(x: float, y: float) -> Point:
    x = min(max(x, X_MIN), X_MAX)
    y = min(max(y, Y_MIN), Y_MAX)
    if y < 0 and x < X_MIN_NEG_Y:
        x = X_MIN_NEG_Y
    return x, y


def bounds_of(polylines: list[Polyline]) -> tuple[float, float, float, float] | None:
    """Return (min_x, min_y, max_x, max_y) or None if empty."""
    pts = [p for line in polylines for p in line]
    if not pts:
        return None
    xs = [p[0] for p in pts]
    ys = [p[1] for p in pts]
    return min(xs), min(ys), max(xs), max(ys)


def fit_polylines_to_area(
    polylines: list[Polyline], margin: float = 25.0
) -> list[Polyline]:
    """Uniformly scale and center polylines into the safe rectangle."""
    bb = bounds_of(polylines)
    if bb is None:
        return []
    min_x, min_y, max_x, max_y = bb
    src_w = max(max_x - min_x, 1e-9)
    src_h = max(max_y - min_y, 1e-9)
    dst_w = (X_MAX - X_MIN) - 2 * margin
    dst_h = (Y_MAX - Y_MIN) - 2 * margin
    scale = min(dst_w / src_w, dst_h / src_h)
    cx_src = (min_x + max_x) / 2
    cy_src = (min_y + max_y) / 2
    cx_dst = (X_MIN + X_MAX) / 2
    cy_dst = (Y_MIN + Y_MAX) / 2
    return [
        [
            (
                (x - cx_src) * scale + cx_dst,
                (y - cy_src) * scale + cy_dst,
            )
            for x, y in line
        ]
        for line in polylines
    ]


def fit_pixels_to_machine(
    polylines: list[Polyline], margin: float = 25.0
) -> list[Polyline]:
    """Fit pixel-space polylines (y down, like images) into the drawing area.

    Pixels are uniformly scaled and centered onto the paper, preserving the
    on-screen orientation, then converted to machine coordinates.
    """
    bb = bounds_of(polylines)
    if bb is None:
        return []
    min_x, min_y, max_x, max_y = bb
    src_w = max(max_x - min_x, 1e-9)
    src_h = max(max_y - min_y, 1e-9)
    dst_w = PAPER_W - 2 * margin
    dst_h = PAPER_H - 2 * margin
    scale = min(dst_w / src_w, dst_h / src_h)
    off_x = (PAPER_W - src_w * scale) / 2
    off_y = (PAPER_H - src_h * scale) / 2
    return [
        [
            paper_to_machine(
                (x - min_x) * scale + off_x,
                (y - min_y) * scale + off_y,
            )
            for x, y in line
        ]
        for line in polylines
    ]


def sort_paths(polylines: list[Polyline]) -> list[Polyline]:
    """Greedy nearest-neighbour ordering to reduce pen-up travel.

    Paths may be reversed when their end is closer than their start.
    """
    remaining = [line for line in polylines if len(line) >= 2]
    if not remaining:
        return [line for line in polylines if line]
    ordered: list[Polyline] = []
    pos = (HOME[0], HOME[1])
    while remaining:
        best_i = 0
        best_rev = False
        best_d = math.inf
        for i, line in enumerate(remaining):
            d_start = (line[0][0] - pos[0]) ** 2 + (line[0][1] - pos[1]) ** 2
            d_end = (line[-1][0] - pos[0]) ** 2 + (line[-1][1] - pos[1]) ** 2
            if d_start < best_d:
                best_d, best_i, best_rev = d_start, i, False
            if d_end < best_d:
                best_d, best_i, best_rev = d_end, i, True
        line = remaining.pop(best_i)
        if best_rev:
            line = line[::-1]
        ordered.append(line)
        pos = line[-1]
    return ordered


def simplify(polyline: Polyline, epsilon: float) -> Polyline:
    """Douglas-Peucker simplification (epsilon in machine units)."""
    if len(polyline) < 3 or epsilon <= 0:
        return polyline
    arr = np.array(polyline, dtype=np.float32).reshape(-1, 1, 2)
    out = cv2.approxPolyDP(arr, epsilon, False)
    return [(float(p[0][0]), float(p[0][1])) for p in out]


def total_segments(polylines: list[Polyline]) -> int:
    return sum(max(len(line) - 1, 0) for line in polylines)
