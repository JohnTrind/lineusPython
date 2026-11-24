"""Convert an uploaded image into drawable polylines (machine coordinates).

Pipeline (linedraw-style): grayscale → blur → Canny edges → contours →
Douglas-Peucker simplification, plus optional diagonal hatching driven by
2-3 tone levels for shading. The result is sorted (nearest-neighbour) and
fitted to the safe drawing area.
"""

from __future__ import annotations

from dataclasses import dataclass

import cv2
import numpy as np

from . import geometry

MAX_DIM = 900  # work resolution; more detail than the robot can express anyway


@dataclass
class ImageParams:
    detail: int = 50          # 0-100, higher = more edges (lower Canny thresholds)
    simplify: float = 1.0     # Douglas-Peucker epsilon in work pixels
    hatching: bool = False
    hatch_density: int = 50   # 0-100, higher = tighter hatch spacing
    invert: bool = False
    min_path_len: int = 3     # drop tiny specks (in points)


def convert(data: bytes, params: ImageParams) -> tuple[list[geometry.Polyline], list[str]]:
    arr = np.frombuffer(data, dtype=np.uint8)
    img = cv2.imdecode(arr, cv2.IMREAD_GRAYSCALE)
    if img is None:
        raise ValueError("Imagem inválida ou formato não suportado")
    warnings: list[str] = []

    scale = MAX_DIM / max(img.shape)
    if scale < 1:
        img = cv2.resize(img, None, fx=scale, fy=scale, interpolation=cv2.INTER_AREA)
    if params.invert:
        img = 255 - img

    polylines = _contours(img, params)
    if params.hatching:
        polylines += _hatching(img, params)

    if not polylines:
        warnings.append("Nenhuma linha extraída — tenta aumentar o detalhe ou ativar hatching.")
        return [], warnings

    polylines = geometry.fit_pixels_to_machine(polylines)
    polylines = geometry.sort_paths(polylines)

    segs = geometry.total_segments(polylines)
    if segs > 8000:
        warnings.append(
            f"{segs} segmentos — vai demorar bastante; considera baixar o detalhe "
            "ou aumentar a simplificação."
        )
    return polylines, warnings


def _contours(img: np.ndarray, params: ImageParams) -> list[geometry.Polyline]:
    blurred = cv2.GaussianBlur(img, (5, 5), 0)
    detail = max(0, min(100, params.detail))
    # detail 0 → thresholds (200, 250): few edges; detail 100 → (20, 60): many.
    low = int(200 - 1.8 * detail)
    high = int(250 - 1.9 * detail)
    edges = cv2.Canny(blurred, low, high)
    edges = cv2.dilate(edges, np.ones((2, 2), np.uint8))
    contours, _ = cv2.findContours(edges, cv2.RETR_LIST, cv2.CHAIN_APPROX_SIMPLE)
    out: list[geometry.Polyline] = []
    eps = max(0.1, params.simplify)
    min_pts = max(2, params.min_path_len)
    for contour in contours:
        approx = cv2.approxPolyDP(contour, eps, False)
        if len(approx) < min_pts:
            continue
        out.append([(float(p[0][0]), float(p[0][1])) for p in approx])
    return out


def _hatching(img: np.ndarray, params: ImageParams) -> list[geometry.Polyline]:
    """Diagonal hatch lines where the image is dark; darker = second pass."""
    density = max(0, min(100, params.hatch_density))
    spacing = int(24 - 0.18 * density)  # px between hatch lines: 24 → 6
    smooth = cv2.GaussianBlur(img, (9, 9), 0)
    h, w = smooth.shape
    out: list[geometry.Polyline] = []
    # Two tone levels: <128 hatched one way, <64 cross-hatched.
    for level, direction in ((128, 1), (64, -1)):
        mask = smooth < level
        if not mask.any():
            continue
        for c0 in range(-h, w, spacing):
            run: geometry.Polyline = []
            for yy in range(h):
                xx = c0 + direction * yy
                if 0 <= xx < w and mask[yy, xx]:
                    run.append((float(xx), float(yy)))
                elif len(run) >= 2:
                    out.append([run[0], run[-1]])
                    run = []
                else:
                    run = []
            if len(run) >= 2:
                out.append([run[0], run[-1]])
    return out
