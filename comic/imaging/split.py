"""Detect comic panels on a page (recursive XY-cut along near-white gutters) and trim panel borders."""

from __future__ import annotations

from PIL import Image

Box = tuple[int, int, int, int]


def _dark_mask(gray: Image.Image, white: int) -> Image.Image:
    """255 where a pixel is darker than `white` (i.e. not gutter), else 0."""
    return gray.point(lambda v: 255 if v < white else 0)


def _profiles(mask: Image.Image, box: Box) -> tuple[list[bool], list[bool]]:
    """(rows_white, cols_white) for the region: True where the whole line has no dark pixel."""
    x0, y0, x1, y1 = box
    rows = [mask.crop((x0, y, x1, y + 1)).getbbox() is None for y in range(y0, y1)]
    cols = [mask.crop((x, y0, x + 1, y1)).getbbox() is None for x in range(x0, x1)]
    return rows, cols


def _white_runs(profile: list[bool], min_len: int) -> list[tuple[int, int]]:
    runs, start = [], None
    for i, ok in enumerate(profile + [False]):
        if ok and start is None:
            start = i
        elif not ok and start is not None:
            if i - start >= min_len:
                runs.append((start, i))
            start = None
    return runs


def _cut(mask: Image.Image, box: Box, min_gutter: int, rtl: bool) -> list[Box]:
    x0, y0, x1, y1 = box
    if x1 <= x0 or y1 <= y0:
        return []
    bbox = mask.crop(box).getbbox()  # trims white margins
    if bbox is None:
        return []
    tight = (x0 + bbox[0], y0 + bbox[1], x0 + bbox[2], y0 + bbox[3])
    if tight != box:
        return _cut(mask, tight, min_gutter, rtl)
    rows, cols = _profiles(mask, box)
    for profile, horizontal in ((rows, True), (cols, False)):
        gutters = _white_runs(profile, min_gutter)
        if not gutters:
            continue
        parts, prev = [], 0
        for s, e in gutters + [(len(profile), len(profile))]:
            if s > prev:
                parts.append((x0, y0 + prev, x1, y0 + s) if horizontal else (x0 + prev, y0, x0 + s, y1))
            prev = e
        if not horizontal and rtl:
            parts.reverse()
        out: list[Box] = []
        for b in parts:
            out += _cut(mask, b, min_gutter, rtl)
        return out
    return [box]


def detect_panels(img: Image.Image, white: int = 235, min_gutter: int = 8,
                  reading_direction: str = "ltr", min_area: float = 0.02) -> list[Box]:
    """Panel boxes (x0, y0, x1, y1; x1/y1 exclusive) in reading order.

    Rows top to bottom; side-by-side panels left to right, or right to left
    when reading_direction is 'rtl'. Boxes smaller than min_area of the page are dropped.
    """
    gray = img.convert("L")
    mask = _dark_mask(gray, white)
    boxes = _cut(mask, (0, 0, img.width, img.height), min_gutter, reading_direction == "rtl")
    limit = min_area * img.width * img.height
    return [b for b in boxes if (b[2] - b[0]) * (b[3] - b[1]) > limit]


# --------------------------------------------------------------------------- border trim


def _line_means(gray: Image.Image, side: str, depth: int) -> list[float]:
    """Mean luminance of the first `depth` lines from one side (excluding the 2 % corners)."""
    w, h = gray.size
    means = []
    for d in range(depth):
        if side == "top":
            line = gray.crop((0, d, w, d + 1))
        elif side == "bottom":
            line = gray.crop((0, h - 1 - d, w, h - d))
        elif side == "left":
            line = gray.crop((d, 0, d + 1, h))
        else:
            line = gray.crop((w - 1 - d, 0, w - d, h))
        # mean via a 1x1 box downscale (C speed)
        means.append(float(line.resize((1, 1), Image.BOX).getpixel((0, 0))))
    return means


def border_widths(img: Image.Image, max_frac: float = 0.02, max_px: int = 16) -> dict[str, int]:
    """Pixels of drawn dark border on each side: lines clearly darker than the panel just inside them."""
    gray = img.convert("L")
    w, h = gray.size
    result = {}
    for side in ("top", "bottom", "left", "right"):
        dim = h if side in ("top", "bottom") else w
        depth = max(4, min(max_px, int(dim * max_frac)))
        if dim < depth * 4:
            result[side] = 0
            continue
        means = _line_means(gray, side, depth + 8)
        inner = sorted(means[depth:depth + 8])
        ref = inner[len(inner) // 2]  # median of the lines just inside the border zone
        delta = max(18.0, 0.15 * ref)
        # the border ends at the first sharp brightening after a dark line near the edge
        last = -1
        if means[0] < ref - delta or means[1] < ref - delta:
            for d in range(1, depth):
                if means[d - 1] < ref - delta and means[d] - means[d - 1] > delta:
                    last = d - 1
                    break
        trim = last + 1
        # one more line if it is a half-dark anti-aliased edge
        if last >= 0 and trim < depth and means[trim] < ref - delta / 2:
            trim += 1
        result[side] = trim
    return result


def trim_border(img: Image.Image) -> Image.Image:
    """Strip the drawn dark panel border (1-3 px, possibly gray) from a panel crop."""
    b = border_widths(img)
    w, h = img.size
    box = (b["left"], b["top"], w - b["right"], h - b["bottom"])
    if box[2] - box[0] < w // 2 or box[3] - box[1] < h // 2:
        return img
    return img.crop(box) if box != (0, 0, w, h) else img


def parse_boxes(text: str) -> list[Box]:
    """'x0,y0,x1,y1;x0,y0,x1,y1' -> boxes. Raises ValueError with a readable message."""
    boxes = []
    for part in str(text).split(";"):
        part = part.strip()
        if not part:
            continue
        nums = [p.strip() for p in part.split(",")]
        if len(nums) != 4:
            raise ValueError(f"box '{part}' needs 4 numbers x0,y0,x1,y1")
        x0, y0, x1, y1 = (int(round(float(n))) for n in nums)
        if x1 <= x0 or y1 <= y0:
            raise ValueError(f"box '{part}' must have x1 > x0 and y1 > y0")
        boxes.append((x0, y0, x1, y1))
    return boxes


def format_boxes(boxes) -> str:
    return ";".join(",".join(str(v) for v in b) for b in boxes)
