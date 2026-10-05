"""Lay panels out onto the exact output canvas: cover-fit into slots, focus crop, border."""

from __future__ import annotations

from PIL import Image, ImageDraw

from comic.layout import slot_boxes

Box = tuple[int, int, int, int]


def cover_fit(img: Image.Image, width: int, height: int, focus: float = 0.5) -> tuple[Image.Image, float]:
    """Scale img to cover width x height and crop. focus picks the vertical anchor
    (0 keep top, 1 keep bottom); horizontal crops are centred. Returns (image, cropped area fraction)."""
    focus = min(1.0, max(0.0, float(0.5 if focus is None else focus)))
    scale = max(width / img.width, height / img.height)
    rw, rh = max(width, round(img.width * scale)), max(height, round(img.height * scale))
    resized = img.resize((rw, rh), Image.LANCZOS)
    left = (rw - width) // 2
    top = round((rh - height) * focus)
    crop = 1.0 - (width * height) / (rw * rh)
    return resized.crop((left, top, left + width, top + height)), crop


def compose(panels, preset: dict, weights, focuses=None, report: list | None = None) -> tuple[Image.Image, list[Box]]:
    """Compose panel images onto preset width x height.

    Slots come from comic.layout.slot_boxes (rows layout); `layout.border` px is drawn
    inside each slot. If `report` is a list, one dict per slot is appended:
    {"size": (w, h), "crop": fraction of the scaled panel cut away}.
    """
    width, height = int(preset["width"]), int(preset["height"])
    lay = preset.get("layout") or {}
    margin, gutter, border = int(lay.get("margin", 36)), int(lay.get("gutter", 24)), int(lay.get("border", 4))
    panels = list(panels)
    weights = list(weights)
    focuses = list(focuses) if focuses is not None else [0.5] * len(panels)
    if not (len(panels) == len(weights) == len(focuses)):
        raise ValueError("panels, weights and focuses must have the same length")
    boxes = slot_boxes(width, height, weights, margin, gutter)
    canvas = Image.new("RGB", (width, height), "white")
    draw = ImageDraw.Draw(canvas)
    for img, box, focus in zip(panels, boxes, focuses):
        x0, y0, x1, y1 = box
        fitted, crop = cover_fit(img.convert("RGB"), x1 - x0, y1 - y0, focus)
        canvas.paste(fitted, (x0, y0))
        if border > 0:
            draw.rectangle([x0, y0, x1 - 1, y1 - 1], outline="black", width=border)
        if report is not None:
            report.append({"size": (x1 - x0, y1 - y0), "crop": crop})
    return canvas, boxes


def layout_json(canvas_size, panel_ids, boxes) -> dict:
    """The composed/PAGE.layout.json document."""
    return {"canvas": [int(canvas_size[0]), int(canvas_size[1])],
            "slots": {str(pid): [int(v) for v in box] for pid, box in zip(panel_ids, boxes)}}
