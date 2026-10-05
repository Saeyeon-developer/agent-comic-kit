"""Panel slot geometry shared by prompt assembly and compose."""

from __future__ import annotations

from math import gcd


def slot_boxes(canvas_w: int, canvas_h: int, weights, margin: int, gutter: int) -> list[tuple[int, int, int, int]]:
    """Rows layout: panels stacked top to bottom at full content width.

    Slot heights are proportional to weights; the last slot absorbs rounding.
    Boxes are (x0, y0, x1, y1) with x1/y1 exclusive.
    """
    weights = [float(w) for w in weights]
    if not weights:
        return []
    if any(w <= 0 for w in weights):
        raise ValueError("panel weights must be positive")
    n = len(weights)
    x0, x1 = margin, canvas_w - margin
    avail = canvas_h - 2 * margin - gutter * (n - 1)
    if x1 <= x0 or avail < n:
        raise ValueError(f"canvas {canvas_w}x{canvas_h} too small for {n} panels with margin {margin}, gutter {gutter}")
    total = sum(weights)
    boxes = []
    y = margin
    for i, w in enumerate(weights):
        h = round(avail * w / total) if i < n - 1 else canvas_h - margin - y
        boxes.append((x0, y, x1, y + h))
        y += h + gutter
    return boxes


def aspect_text(w: float, h: float) -> str:
    """Reduced aspect ratio for prompts.

    Integer form when within 1% of a ratio with both terms <= 10
    (1080x1350 -> "4:5", 1536x1024 -> "3:2"); otherwise "2.39:1" (landscape)
    or "1:1.25" (portrait).
    """
    if w <= 0 or h <= 0:
        raise ValueError("width and height must be positive")
    r = w / h
    best = None
    for b in range(1, 11):
        for a in range(1, 11):
            if gcd(a, b) != 1:
                continue
            err = abs(a / b - r) / r
            if err <= 0.01 and (best is None or err < best[0] - 1e-12):
                best = (err, a, b)
    if best:
        return f"{best[1]}:{best[2]}"
    if r >= 1:
        return f"{r:.2f}:1"
    return f"1:{1 / r:.2f}"
