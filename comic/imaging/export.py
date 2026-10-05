"""Export finished pages as upload-ready files (slides or scroll slices)."""

from __future__ import annotations

import io
import re
from pathlib import Path

from PIL import Image

from comic.project import ComicError

MIN_JPEG_QUALITY = 60
QUALITY_STEP = 4
_EXPORT_NAME = re.compile(r"^\d{2,}\.(jpg|jpeg|png|webp)$", re.IGNORECASE)

_FORMATS = {"jpg": ("JPEG", ".jpg"), "jpeg": ("JPEG", ".jpg"), "png": ("PNG", ".png"), "webp": ("WEBP", ".webp")}


def file_settings(preset: dict) -> tuple[str, str, int, float | None]:
    """(PIL format, extension, quality, max_bytes or None) from preset['file']."""
    f = preset.get("file") or {}
    ftype = str(f.get("type") or "jpg").lower()
    if ftype not in _FORMATS:
        raise ComicError(f"preset file.type '{ftype}' is not supported (use jpg, png or webp)")
    fmt, ext = _FORMATS[ftype]
    quality = int(f.get("quality") or 92)
    max_mb = f.get("max_mb")
    max_bytes = float(max_mb) * 1024 * 1024 if max_mb else None
    return fmt, ext, quality, max_bytes


def encode(img: Image.Image, fmt: str, quality: int, max_bytes: float | None, label: str = "image") -> tuple[bytes, int]:
    """Encode img; for JPEG/WebP lower quality by 4 (down to 60) until it fits max_bytes.

    Returns (data, quality used). Raises ComicError when it cannot fit.
    """
    img = img.convert("RGB")
    q = quality
    while True:
        buf = io.BytesIO()
        if fmt == "PNG":
            img.save(buf, "PNG", optimize=True)
        else:
            img.save(buf, fmt, quality=q, **({"optimize": True} if fmt == "JPEG" else {}))
        data = buf.getvalue()
        if max_bytes is None or len(data) <= max_bytes:
            return data, q
        if fmt == "PNG":
            raise ComicError(f"{label} is {len(data) / 1048576:.1f} MB as PNG, over the preset max_mb "
                             f"{max_bytes / 1048576:g}; use file.type jpg or raise max_mb in format.overrides")
        if q - QUALITY_STEP < MIN_JPEG_QUALITY:
            raise ComicError(f"{label} is still {len(data) / 1048576:.1f} MB at quality {q}, over max_mb "
                             f"{max_bytes / 1048576:g}; reduce the canvas size or raise max_mb in format.overrides")
        q -= QUALITY_STEP


def clear_previous(out_dir: Path) -> None:
    """Remove files of a previous export (NN.ext in out/ and out/post-N/); keeps everything else."""
    if not out_dir.is_dir():
        return
    dirs = [out_dir] + [d for d in out_dir.iterdir() if d.is_dir() and re.fullmatch(r"post-\d+", d.name)]
    for d in dirs:
        for p in d.iterdir():
            if p.is_file() and _EXPORT_NAME.match(p.name):
                p.unlink()
        if d is not out_dir and not any(d.iterdir()):
            d.rmdir()


def slide_targets(out_dir: Path, count: int, per_post, ext: str) -> list[Path]:
    """Output paths for `count` slides: out/01.jpg … or out/post-N/01.jpg … when per_post is set."""
    if per_post:
        per_post = int(per_post)
        return [out_dir / f"post-{i // per_post + 1}" / f"{i % per_post + 1:02d}{ext}" for i in range(count)]
    return [out_dir / f"{i + 1:02d}{ext}" for i in range(count)]


def stitch(images: list[Image.Image], width: int) -> Image.Image:
    """Stack images top to bottom (each scaled to `width` if needed)."""
    scaled = []
    for img in images:
        img = img.convert("RGB")
        if img.width != width:
            img = img.resize((width, round(img.height * width / img.width)), Image.LANCZOS)
        scaled.append(img)
    strip = Image.new("RGB", (width, sum(i.height for i in scaled)), "white")
    y = 0
    for img in scaled:
        strip.paste(img, (0, y))
        y += img.height
    return strip


def slices(strip: Image.Image, slice_height: int) -> list[Image.Image]:
    return [strip.crop((0, y, strip.width, min(strip.height, y + slice_height)))
            for y in range(0, strip.height, slice_height)]


def write_files(images: list[Image.Image], targets: list[Path], preset: dict) -> list[tuple[Path, int, int]]:
    """Encode and write images; returns [(path, bytes, quality)]."""
    fmt, _ext, quality, max_bytes = file_settings(preset)
    written = []
    for img, path in zip(images, targets):
        data, q = encode(img, fmt, quality, max_bytes, label=path.name)
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(data)
        written.append((path, len(data), q))
    return written
