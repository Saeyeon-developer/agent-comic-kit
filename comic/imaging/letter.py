"""Overlay speech bubbles (speech / shout / thought / narration) onto a composed page.

Geometry here is in page pixels; the command layer converts the fractional
placements of lettering.yaml (fractions of the panel slot) to pixels.
"""

from __future__ import annotations

import math
from dataclasses import dataclass, field
from functools import lru_cache

from PIL import Image, ImageDraw, ImageFont

SS = 3  # supersampling factor
INK, PAPER = (20, 20, 20, 255), (255, 255, 255, 255)
WEIGHT = {"speech": 600, "shout": 900, "thought": 500, "narration": 500}
KINDS = tuple(WEIGHT)
LINE_HEIGHT = 1.32
TAIL_MAX = 90  # px at a 1080 px wide canvas, measured from the outer edge of the body
TAIL_MIN = {"speech": 24, "shout": 24, "thought": 40}  # shortest drawable tail, px at 1080
DEFAULT_WIDTH = 0.24  # max text width, fraction of the slot (auto placement, and placements without width)
CLOSING = set("!?.,…・、。，．！？）」』】〉》)]}~ー-")


# --------------------------------------------------------------------------- fonts


@lru_cache(maxsize=64)
def load_font(path: str, size: int, weight: int) -> ImageFont.FreeTypeFont:
    font = ImageFont.truetype(str(path), max(1, int(size)))
    try:
        font.set_variation_by_axes([weight])
    except Exception:  # not a variable font
        pass
    return font


# --------------------------------------------------------------------------- wrapping


def _tokens(para: str, font, width: float) -> list[tuple[str, str]]:
    """(text, separator-before) chunks. Space-separated words; a word wider than
    `width` is split into characters (closing punctuation stays with the previous character)."""
    toks: list[tuple[str, str]] = []
    for word in para.split(" "):
        if not word:
            continue
        if font.getlength(word) <= width:
            toks.append((word, " "))
            continue
        first = True
        for ch in word:
            if not first and ch in CLOSING:
                text, sep = toks[-1]
                toks[-1] = (text + ch, sep)
            else:
                toks.append((ch, " " if first else ""))
            first = False
    return toks


def _greedy(toks, font, width: float) -> list[str]:
    lines, line = [], ""
    for text, sep in toks:
        cand = line + (sep if line else "") + text
        if not line or font.getlength(cand) <= width:
            line = cand
        else:
            lines.append(line)
            line = text
    if line:
        lines.append(line)
    return lines


def wrap_balanced(text: str, font, width: float) -> list[str]:
    """Wrap to at most `width` px with lines of similar length.

    The line count is what greedy wrapping needs at `width`; then the narrowest
    width that keeps that count is searched, which evens the lines out. Breaks
    at spaces; inside a word only when the word alone is wider than `width`.
    """
    lines: list[str] = []
    for para in str(text).split("\n"):
        toks = _tokens(para, font, width)
        if not toks:
            lines.append("")
            continue
        best = _greedy(toks, font, width)
        n = len(best)
        if n > 1:
            lo = max(font.getlength(t) for t, _ in toks)
            hi = float(width)
            for _ in range(14):
                mid = (lo + hi) / 2
                if len(_greedy(toks, font, mid)) <= n:
                    hi = mid
                else:
                    lo = mid
            cand = _greedy(toks, font, hi)
            if len(cand) <= n:
                best = cand
        lines += best
    return lines


# --------------------------------------------------------------------------- geometry


def _ellipse_point(cx, cy, a, b, ang):
    return cx + a * math.cos(ang), cy + b * math.sin(ang)


def _angle(cx, cy, a, b, target):
    return math.atan2((target[1] - cy) / b, (target[0] - cx) / a)


def _tail_room(cx, cy, ex, ey, target) -> float:
    """Distance from the body's outer edge (ellipse ex x ey) to target along the ray
    from the centre; negative when target lies inside."""
    dx, dy = target[0] - cx, target[1] - cy
    r = math.hypot(dx / ex, dy / ey)
    if r == 0:
        return -1.0
    d = math.hypot(dx, dy) * abs(1 - 1 / r)
    return d if r > 1 else -d


def _cap_tip(cx, cy, a, b, target, max_len):
    """Move the tail tip so it is at most max_len from the bubble edge."""
    ex, ey = _ellipse_point(cx, cy, a, b, _angle(cx, cy, a, b, target))
    dx, dy = target[0] - ex, target[1] - ey
    dist = math.hypot(dx, dy)
    if dist <= max_len or dist == 0:
        return tuple(target)
    return ex + dx / dist * max_len, ey + dy / dist * max_len


def shape_axes(kind: str, tw: float, th: float, u: float) -> tuple[float, float]:
    """Half-axes (a, b) of the bubble body for a text block tw x th; u = px per design unit."""
    if kind == "narration":
        pad = 18 * u
        return tw / 2 + pad, th / 2 + pad
    return tw / 2 * 1.32 + 20 * u, th / 2 * 1.42 + 18 * u


def shape_extents(kind: str, a: float, b: float, spike: float = 1.22) -> tuple[float, float]:
    """Half-size of everything drawn for the body (spikes, lobes) around the centre."""
    if kind == "shout":
        k = spike + 0.06
        return a * k, b * k
    if kind == "thought":
        r = min(a, b) * 0.42
        return a * 0.92 + r, b * 0.88 + r
    return a, b


def _fit_axis(c, half, lo, hi):
    if hi - lo <= 2 * half:
        return (lo + hi) / 2
    return min(max(c, lo + half), hi - half)


# --------------------------------------------------------------------------- drawing


@dataclass
class Bubble:
    kind: str
    text: str
    at: tuple[float, float]           # centre, page px
    width: float                      # max text line width, page px
    size: float                       # font size, page px
    tail: tuple[float, float] | None = None
    clamp: bool = False
    slot: tuple[int, int, int, int] | None = None   # page px, used by clamp
    key: str = ""
    info: dict = field(default_factory=dict)


def measure(bubble: Bubble, font_path, scale: float) -> tuple[list[str], float, float, float, float]:
    """(lines, text_w, text_h, extent_x, extent_y) in page px (extents without tail)."""
    font = load_font(str(font_path), round(bubble.size * SS), WEIGHT.get(bubble.kind, 600))
    lines = wrap_balanced(bubble.text, font, bubble.width * SS)
    tw = max(font.getlength(line) for line in lines) / SS
    th = bubble.size * LINE_HEIGHT * len(lines)
    a, b = shape_axes(bubble.kind, tw, th, scale)
    ex, ey = shape_extents(bubble.kind, a, b)
    return lines, tw, th, ex, ey


def _draw_body(d, kind, cx, cy, a, b, tip, stroke, u, spike):
    if kind == "narration":
        d.rectangle([cx - a, cy - b, cx + a, cy + b], fill=PAPER, outline=INK, width=stroke)
        return
    if kind == "speech":
        d.ellipse([cx - a - stroke, cy - b - stroke, cx + a + stroke, cy + b + stroke], fill=INK)
        if tip:
            ang = _angle(cx, cy, a, b, tip)
            p1 = _ellipse_point(cx, cy, a * 0.92, b * 0.92, ang - 0.22)
            p2 = _ellipse_point(cx, cy, a * 0.92, b * 0.92, ang + 0.22)
            d.polygon([p1, tuple(tip), p2], fill=PAPER, outline=INK, width=stroke)
        d.ellipse([cx - a, cy - b, cx + a, cy + b], fill=PAPER)
    elif kind == "shout":
        n, pts = 22, []
        tip_ang = _angle(cx, cy, a, b, tip) if tip else None
        for i in range(n * 2):
            ang = 2 * math.pi * i / (n * 2)
            r = 1.0 if i % 2 else spike + 0.06 * math.sin(i * 2.3)
            p = _ellipse_point(cx, cy, a * r, b * r, ang)
            if tip and i % 2 == 0 and abs((ang - tip_ang + math.pi) % (2 * math.pi) - math.pi) < math.pi / n:
                p = tuple(tip)
            pts.append(p)
        d.polygon(pts, fill=PAPER, outline=INK, width=stroke)
    elif kind == "thought":
        lobes = 12
        r = min(a, b) * 0.42
        centres = [_ellipse_point(cx, cy, a * 0.92, b * 0.88, 2 * math.pi * i / lobes) for i in range(lobes)]
        for x, y in centres:
            d.ellipse([x - r - stroke, y - r - stroke, x + r + stroke, y + r + stroke], fill=INK)
        for x, y in centres:
            d.ellipse([x - r, y - r, x + r, y + r], fill=PAPER)
        d.ellipse([cx - a * 0.95, cy - b * 0.95, cx + a * 0.95, cy + b * 0.95], fill=PAPER)
        if tip:
            # small circles between the outer edge of the cloud and the tip
            oa, ob = shape_extents("thought", a, b)
            sx, sy = _ellipse_point(cx, cy, oa, ob, _angle(cx, cy, oa, ob, tip))
            room = math.hypot(tip[0] - sx, tip[1] - sy)
            k_size = max(0.6, min(1.0, room / (70 * u)))
            for t, rr in ((0.30, 14), (0.62, 9), (0.90, 6)):
                x, y = sx + (tip[0] - sx) * t, sy + (tip[1] - sy) * t
                rr *= u * k_size
                d.ellipse([x - rr, y - rr, x + rr, y + rr], fill=PAPER, outline=INK, width=stroke)


def render(base: Image.Image, bubbles: list[Bubble], font_path, scale: float = 1.0, border: int = 4) -> Image.Image:
    """Draw bubbles over base (3x supersampled). scale = canvas_width / 1080.

    A bubble with clamp=True and a slot keeps its body inside the slot's inner
    border: shout spikes shrink first, then the bubble moves inward.
    Each bubble's `info` gets {"moved": (dx, dy)} in page px when clamping moved it.
    Tails start at the final (clamped) bubble; when the tail target lies inside the
    body or closer than TAIL_MIN to its edge no tail is drawn and `info` gets
    {"tail_skipped": True}.
    """
    base = base.convert("RGBA")
    layer = Image.new("RGBA", (base.width * SS, base.height * SS), (0, 0, 0, 0))
    d = ImageDraw.Draw(layer)
    u = scale * SS
    stroke = max(1, round(3 * u))
    for bb in bubbles:
        kind = bb.kind if bb.kind in WEIGHT else "speech"
        font = load_font(str(font_path), round(bb.size * SS), WEIGHT[kind])
        lines = wrap_balanced(bb.text, font, bb.width * SS)
        lh = bb.size * SS * LINE_HEIGHT
        tw = max(font.getlength(line) for line in lines)
        th = lh * len(lines)
        cx, cy = bb.at[0] * SS, bb.at[1] * SS
        a, b = shape_axes(kind, tw, th, u)
        spike = 1.22
        if bb.clamp and bb.slot:
            x0, y0, x1, y1 = bb.slot
            inset = (border + 2 * scale) * SS
            ix0, iy0, ix1, iy1 = x0 * SS + inset, y0 * SS + inset, x1 * SS - inset, y1 * SS - inset
            ex, ey = shape_extents(kind, a, b, spike)
            if kind == "shout" and (cx - ex < ix0 or cx + ex > ix1 or cy - ey < iy0 or cy + ey > iy1):
                spike = 1.10
                ex, ey = shape_extents(kind, a, b, spike)
            ncx, ncy = _fit_axis(cx, ex, ix0, ix1), _fit_axis(cy, ey, iy0, iy1)
            if (ncx, ncy) != (cx, cy):
                bb.info["moved"] = (round((ncx - cx) / SS), round((ncy - cy) / SS))
            cx, cy = ncx, ncy
        tip = None
        if bb.tail and kind != "narration":
            # the tail always starts from the final (clamped) body position
            ex, ey = shape_extents(kind, a, b, spike)
            tip = _cap_tip(cx, cy, ex, ey, (bb.tail[0] * SS, bb.tail[1] * SS), TAIL_MAX * u)
            if bb.clamp and bb.slot:
                x0, y0, x1, y1 = bb.slot
                tip = (min(max(tip[0], x0 * SS), x1 * SS), min(max(tip[1], y0 * SS), y1 * SS))
            if _tail_room(cx, cy, ex, ey, tip) < TAIL_MIN[kind] * u:
                tip = None  # target inside or right at the edge of the bubble: a tail would be drawn inside
                bb.info["tail_skipped"] = True
        _draw_body(d, kind, cx, cy, a, b, tip, stroke, u, spike)
        for i, line in enumerate(lines):
            y = cy - th / 2 + lh * i + lh / 2
            d.text((cx, y), line, font=font, fill=INK, anchor="mm")
    layer = layer.resize(base.size, Image.LANCZOS)
    return Image.alpha_composite(base, layer).convert("RGB")


# --------------------------------------------------------------------------- automatic placement

_REGIONS_X = {"left": (0.02, 0.60), "right": (0.40, 0.98), "centre": (0.15, 0.85), None: (0.02, 0.98)}


def parse_bubble_space(text) -> tuple[str, str | None]:
    """'top-left' / 'upper right' / 'bottom centre' ... -> (vertical, horizontal). Unparseable -> ('top', None)."""
    t = str(text or "").lower()
    vertical = "bottom" if any(w in t for w in ("bottom", "lower", "below")) else "top"
    if "left" in t:
        horizontal = "left"
    elif "right" in t:
        horizontal = "right"
    elif any(w in t for w in ("centre", "center", "middle")):
        horizontal = "centre"
    else:
        horizontal = None
    return vertical, horizontal


FACE_HALF = 0.16   # half-size of the face box around a listed position, fraction of the panel height
MOUTH_DROP = 0.04  # the mouth is this far below the face centre, fraction of the panel height
TAIL_SHORT = 0.07  # auto tails stop this far before the mouth, fraction of the panel height


def face_boxes(positions: dict, slot_w: float, slot_h: float) -> dict:
    """{name: (x0, y0, x1, y1)} in slot px: a square of FACE_HALF * slot_h on each side
    of every listed face centre (fractions of the slot), clipped to the slot."""
    h = FACE_HALF * slot_h
    out = {}
    for name, (fx, fy) in (positions or {}).items():
        px, py = fx * slot_w, fy * slot_h
        out[name] = (max(0.0, px - h), max(0.0, py - h), min(float(slot_w), px + h), min(float(slot_h), py + h))
    return out


def mouth_point(position, slot_w: float, slot_h: float) -> tuple[float, float]:
    """Where an aimed tail points: just below the face centre, slot px."""
    return position[0] * slot_w, min(position[1] * slot_h + MOUTH_DROP * slot_h, float(slot_h))


def bubble_region(bubble_space, slot_w: float, slot_h: float) -> tuple[float, float, float, float]:
    """The preferred area for bubble centres parsed from bubble_space, slot px."""
    vertical, horizontal = parse_bubble_space(bubble_space)
    rx0, rx1 = (v * slot_w for v in _REGIONS_X[horizontal])
    ry0, ry1 = (0.0, 0.5 * slot_h) if vertical == "top" else (0.5 * slot_h, float(slot_h))
    return rx0, ry0, rx1, ry1


def _entry(e):
    """(key, kind, text, speaker) from a 3- or 4-tuple."""
    return (e[0], e[1], e[2], e[3] if len(e) > 3 else None)


def auto_place(entries, slot_w: int, slot_h: int, bubble_space, reading_direction: str, size_px: float,
               scale: float, measure_fn, positions: dict | None = None) -> dict:
    """Guess placements (fractions of the slot) for dialogue entries of one panel.

    entries: [(key, kind, text[, speaker])] in script order. measure_fn(kind, text,
    width_px, size_px) returns (extent_x, extent_y) of the bubble body in px.

    With `positions` ({character: [x, y]} face centres, fractions of the slot) the
    bubbles are searched for on a grid: never on a face box (see face_boxes) or on
    another bubble, near the speaker's mouth, preferring the bubble_space area and
    keeping the reading order; the tail aims at the speaker's mouth. Narration and
    speakers without a position get no aimed tail.

    Without positions bubbles are packed in reading order into rows inside the
    bubble_space area (top area when it cannot be parsed): left to right (right to
    left for rtl), a new row below (above, for bottom areas) when a row is full.
    Speech/shout/thought get a provisional tail pointing into the panel.
    """
    if not entries:
        return {}
    entries = [_entry(e) for e in entries]
    if positions:
        return _auto_place_faces(entries, slot_w, slot_h, bubble_space, reading_direction, size_px,
                                 scale, measure_fn, positions)
    vertical, horizontal = parse_bubble_space(bubble_space)
    rtl = reading_direction == "rtl"
    rx0, rx1 = (v * slot_w for v in _REGIONS_X[horizontal])
    align = horizontal or ("right" if rtl else "left")
    width_px = max(0.12 * slot_w, min(DEFAULT_WIDTH * slot_w, ((rx1 - rx0) - 44 * scale) / 1.32))
    gap = 0.02 * slot_w
    pad = 0.03 * slot_h
    stagger = 0.03 * slot_h

    sized = [(key, kind, *measure_fn(kind, text, width_px, size_px)) for key, kind, text, _sp in entries]
    rows, row, row_w = [], [], 0.0
    for item in sized:
        w = 2 * item[2]
        if row and row_w + gap + w > rx1 - rx0:
            rows.append(row)
            row, row_w = [], 0.0
        row_w += (gap if row else 0) + w
        row.append(item)
    rows.append(row)

    out = {}
    edge = pad if vertical == "top" else slot_h - pad
    for row in rows:
        total = sum(2 * ex for _, _, ex, _ in row) + gap * (len(row) - 1)
        if align == "left":
            start = rx0
        elif align == "right":
            start = rx1 - total
        else:
            start = (rx0 + rx1 - total) / 2
        row_h = max(2 * ey + k * stagger for k, (_, _, _, ey) in enumerate(row))
        # x positions left to right; reading order runs right to left for rtl
        xs, x = [], start
        for _, _, ex, _ in (reversed(row) if rtl else row):
            xs.append(x + ex)
            x += 2 * ex + gap
        if rtl:
            xs.reverse()
        for k, ((key, kind, ex, ey), cx) in enumerate(zip(row, xs)):
            if vertical == "top":
                cy = edge + ey + k * stagger
            else:
                cy = edge - row_h + ey + k * stagger
            cx = _fit_axis(cx, ex, 0.02 * slot_w, 0.98 * slot_w)
            cy = _fit_axis(cy, ey, 0.02 * slot_h, 0.98 * slot_h)
            place = {"at": [round(cx / slot_w, 3), round(cy / slot_h, 3)], "width": round(width_px / slot_w, 3)}
            if kind != "narration":
                ty = cy + ey + 0.10 * slot_h if vertical == "top" else cy - ey - 0.10 * slot_h
                tx = cx + (0.5 * slot_w - cx) * 0.25
                ty = min(max(ty, 0.02 * slot_h), 0.98 * slot_h)
                place["tail"] = [round(tx / slot_w, 3), round(ty / slot_h, 3)]
            place["auto"] = True
            out[key] = place
        edge = edge + row_h + stagger if vertical == "top" else edge - row_h - stagger
    return out


def _overlap(r1, r2) -> float:
    w = min(r1[2], r2[2]) - max(r1[0], r2[0])
    h = min(r1[3], r2[3]) - max(r1[1], r2[1])
    return w * h if w > 0 and h > 0 else 0.0


def _rect_dist(r, p) -> float:
    dx = max(r[0] - p[0], 0.0, p[0] - r[2])
    dy = max(r[1] - p[1], 0.0, p[1] - r[3])
    return math.hypot(dx, dy)


def _inside(r, p) -> bool:
    return r[0] <= p[0] <= r[2] and r[1] <= p[1] <= r[3]


def _reads_before(n, p, rtl: bool) -> bool:
    """True when bubble rect n would be read before the already placed rect p."""
    if n[3] < p[1]:  # entirely above
        return True
    ncx, pcx = (n[0] + n[2]) / 2, (p[0] + p[2]) / 2
    ncy, pcy = (n[1] + n[3]) / 2, (p[1] + p[3]) / 2
    earlier_side = ncx >= pcx if rtl else ncx <= pcx
    return earlier_side and ncy < pcy + 0.25 * (p[3] - p[1])


def _span(lo, hi, step):
    if hi < lo:
        return [(lo + hi) / 2]
    n = max(1, int((hi - lo) / step))
    return [lo + (hi - lo) * i / n for i in range(n + 1)]


def _auto_place_faces(entries, slot_w, slot_h, bubble_space, reading_direction, size_px, scale, measure_fn,
                      positions) -> dict:
    rtl = reading_direction == "rtl"
    vertical, _horizontal = parse_bubble_space(bubble_space)
    faces = face_boxes(positions, slot_w, slot_h)
    clear = 1.0  # px; keeps placements rounded to 3 decimals off the boxes too
    face_list = [(b[0] - clear, b[1] - clear, b[2] + clear, b[3] + clear) for b in faces.values()]
    # the body below each face: a softer obstacle (bubbles belong above or beside heads)
    bodies = [(max(0.0, b[0] - 0.2 * (b[2] - b[0])), b[3], min(float(slot_w), b[2] + 0.2 * (b[2] - b[0])),
               float(slot_h)) for b in faces.values()]
    region = bubble_region(bubble_space, slot_w, slot_h)
    mx, my = 0.02 * slot_w, 0.02 * slot_h
    gap = 0.015 * slot_w
    base_w = max(0.12 * slot_w, DEFAULT_WIDTH * slot_w)
    widths = [base_w, max(0.12 * slot_w, 0.75 * base_w), max(0.12 * slot_w, 0.55 * base_w)]
    reach = TAIL_MAX * scale + TAIL_SHORT * slot_h  # farthest mouth a capped tail still points at
    corner = (float(slot_w) if rtl else 0.0, 0.0 if vertical == "top" else float(slot_h))
    placed: list[tuple[float, float, float, float]] = []
    out = {}
    for key, kind, text, speaker in entries:
        aimed = kind != "narration" and speaker in positions
        mouth = mouth_point(positions[speaker], slot_w, slot_h) if aimed else None
        best = None
        for wi, wpx in enumerate(widths):
            ex, ey = measure_fn(kind, text, wpx, size_px)
            area = max(1.0, 4 * ex * ey)
            for cx in _span(mx + ex, slot_w - mx - ex, slot_w / 48):
                for cy in _span(my + ey, slot_h - my - ey, slot_h / 48):
                    r = (cx - ex, cy - ey, cx + ex, cy + ey)
                    cost = 0.15 * wi
                    for box in face_list:
                        hit = _overlap(r, box)
                        if hit and kind == "narration":  # narration ignores positions: only a soft nudge
                            cost += 12 * hit / area
                        elif hit:  # never on a face, unless nothing else fits
                            cost += 100 + 12 * hit / area
                    for box in bodies:
                        cost += 1.5 * _overlap(r, box) / area
                    padded = (r[0] - gap, r[1] - gap, r[2] + gap, r[3] + gap)
                    for q in placed:
                        cost += 12 * _overlap(padded, q) / area
                        if _reads_before(r, q, rtl):
                            cost += 1.5
                    # centre inside the bubble_space area, and close to its top (bottom) edge
                    cost += 1.0 * (max(region[0] - cx, 0, cx - region[2]) / slot_w
                                   + max(region[1] - cy, 0, cy - region[3]) / slot_h)
                    cost += 0.3 * (cy / slot_h if vertical == "top" else 1 - cy / slot_h)
                    if mouth:
                        dist = _rect_dist(r, mouth)
                        cost += 2.5 * dist / slot_h + 6 * max(0.0, dist - reach) / slot_h
                        cost += 1.5 * max(0.0, cy - mouth[1]) / slot_h  # below the speaker's mouth
                        # a tail crossing someone else's face reads as the wrong speaker
                        for name, box in faces.items():
                            if name == speaker or _inside(box, mouth):
                                continue
                            if any(_inside(box, (cx + (mouth[0] - cx) * t, cy + (mouth[1] - cy) * t))
                                   for t in (0.2, 0.4, 0.6, 0.8)):
                                cost += 0.8
                                break
                    elif kind == "narration":
                        cost += 1.0 * math.hypot(cx - corner[0], cy - corner[1]) / slot_h
                    if best is None or cost < best[0]:
                        best = (cost, cx, cy, ex, ey, wpx)
        _cost, cx, cy, ex, ey, wpx = best
        rect = (cx - ex, cy - ey, cx + ex, cy + ey)
        placed.append(rect)
        place = {"at": [round(cx / slot_w, 3), round(cy / slot_h, 3)], "width": round(wpx / slot_w, 3)}
        if mouth:
            # stop a little before the mouth so the tail does not cover the face
            dx, dy = cx - mouth[0], cy - mouth[1]
            dist = math.hypot(dx, dy) or 1.0
            short = min(TAIL_SHORT * slot_h, max(0.0, _rect_dist(rect, mouth) - 0.05 * slot_h))
            tx, ty = mouth[0] + dx / dist * short, mouth[1] + dy / dist * short
            place["tail"] = [round(tx / slot_w, 3), round(ty / slot_h, 3)]
        elif kind != "narration":
            below = cy < slot_h / 2
            ty = cy + ey + 0.10 * slot_h if below else cy - ey - 0.10 * slot_h
            tx = cx + (0.5 * slot_w - cx) * 0.25
            ty = min(max(ty, 0.02 * slot_h), 0.98 * slot_h)
            place["tail"] = [round(tx / slot_w, 3), round(ty / slot_h, 3)]
        place["auto"] = True
        out[key] = place
    return out


# --------------------------------------------------------------------------- debug overlay


def debug_overlay(img: Image.Image, slots: dict, faces: dict, mouths: dict, regions: dict) -> Image.Image:
    """Translucent overlay of what auto placement assumed, all in page px:
    slots {id: box} (blue outline), regions {id: box} bubble_space area (green),
    faces {label: box} (red), mouths {label: (x, y)} (red dot)."""
    base = img.convert("RGBA")
    layer = Image.new("RGBA", base.size, (0, 0, 0, 0))
    d = ImageDraw.Draw(layer)
    lw = max(2, round(base.width / 360))
    try:
        font = ImageFont.load_default(size=max(12, base.width // 60))
    except TypeError:  # Pillow < 10.1
        font = ImageFont.load_default()
    for _sid, box in regions.items():
        d.rectangle(box, fill=(60, 200, 90, 40), outline=(60, 200, 90, 200), width=lw)
    for sid, box in slots.items():
        d.rectangle(box, outline=(40, 110, 255, 220), width=lw * 2)
        d.text((box[0] + 4 * lw, box[1] + 3 * lw), f"slot {sid}", fill=(40, 110, 255, 255), font=font)
    for label, box in faces.items():
        d.rectangle(box, fill=(255, 40, 40, 60), outline=(255, 40, 40, 220), width=lw)
        d.text((box[0] + 3 * lw, box[3] - 3 * lw), label, fill=(255, 40, 40, 255), font=font, anchor="ld")
    for _label, (x, y) in mouths.items():
        r = 3 * lw
        d.ellipse([x - r, y - r, x + r, y + r], fill=(255, 40, 40, 230))
    return Image.alpha_composite(base, layer).convert("RGB")
