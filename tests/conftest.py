"""Shared fixtures: synthetic comic pages (no image files are needed in the repository).

The pages mimic what a generated two-panel page looks like to the imaging code: a white page
background with uneven outer margins, white gutters, dark-gray panel borders and busy panel content
(gradients plus random shapes from a seeded RNG) that contains near-white "sky" regions, so the XY-cut
cannot be fooled by white areas inside a panel. The expected panel boxes are computed from the layout.
"""

from __future__ import annotations

import random
from dataclasses import dataclass
from pathlib import Path

import pytest
from PIL import Image, ImageDraw

Box = tuple[int, int, int, int]


@dataclass(frozen=True)
class PageSpec:
    size: tuple[int, int]
    margins: tuple[int, int, int, int]  # left, top, right, bottom (white page background)
    gutter: int  # white space between the stacked panels
    weights: tuple[int, int]  # relative panel heights, top to bottom
    borders: tuple[tuple[int, tuple[int, int, int]], ...]  # (width, colour) per panel
    seed: int


@dataclass(frozen=True)
class SamplePage:
    path: Path
    spec: PageSpec
    boxes: list[Box]  # expected detect_panels() result: outer box of each panel, border included

    @property
    def size(self) -> tuple[int, int]:
        return self.spec.size


# 1024x1536 (2:3): full-bleed panel width, ~4 % top / ~5 % bottom white margin, 17 px gutter
PAGE_2X3 = PageSpec(size=(1024, 1536), margins=(0, 62, 0, 81), gutter=17, weights=(64, 36),
                    borders=((3, (82, 82, 86)), (2, (34, 34, 38))), seed=11)
# 1122x1402 (4:5): ~1.3 % margin all round, 18 px gutter
PAGE_4X5 = PageSpec(size=(1122, 1402), margins=(15, 18, 15, 18), gutter=18, weights=(55, 45),
                    borders=((2, (96, 94, 100)), (3, (60, 60, 60))), seed=29)


def panel_boxes(spec: PageSpec) -> list[Box]:
    """Panel boxes (x0, y0, x1, y1; x1/y1 exclusive) derived from the layout numbers."""
    w, h = spec.size
    left, top, right, bottom = spec.margins
    avail = h - top - bottom - spec.gutter
    h1 = round(avail * spec.weights[0] / sum(spec.weights))
    y1 = top + h1
    y2 = y1 + spec.gutter
    return [(left, top, w - right, y1), (left, y2, w - right, h - bottom)]


def _lerp(a: tuple[int, int, int], b: tuple[int, int, int], t: float) -> tuple[int, int, int]:
    return tuple(round(a[i] + (b[i] - a[i]) * t) for i in range(3))


def _gradient(size: tuple[int, int], c0, c1, vertical: bool) -> Image.Image:
    w, h = size
    img = Image.new("RGB", size)
    d = ImageDraw.Draw(img)
    n = h if vertical else w
    for i in range(n):
        colour = _lerp(c0, c1, i / max(1, n - 1))
        d.line([(0, i), (w, i)] if vertical else [(i, 0), (i, h)], fill=colour)
    return img


def _panel_content(size: tuple[int, int], rng: random.Random) -> Image.Image:
    """A busy, mostly mid-tone image with a near-white sky band, clouds and a near-white wall patch."""
    w, h = size
    base = _gradient(size, (rng.randint(120, 170), rng.randint(150, 200), rng.randint(110, 170)),
                     (rng.randint(170, 215), rng.randint(110, 160), rng.randint(130, 190)), vertical=False)
    top = _gradient(size, (255, 255, 255), (130, 140, 150), vertical=True)
    img = Image.blend(base, top, 0.25)
    d = ImageDraw.Draw(img)
    # random shapes, never darker than mid grey so the drawn border stays the darkest thing on the page
    for _ in range(46):
        x, y = rng.randint(0, w), rng.randint(0, h)
        sw, sh = rng.randint(w // 30, w // 5), rng.randint(h // 30, h // 4)
        colour = tuple(rng.randint(120, 235) for _ in range(3))
        kind = rng.choice(("ellipse", "rect", "poly"))
        if kind == "ellipse":
            d.ellipse([x, y, x + sw, y + sh], fill=colour)
        elif kind == "rect":
            d.rectangle([x, y, x + sw, y + sh], fill=colour)
        else:
            d.polygon([(x, y), (x + sw, y + rng.randint(-sh, sh)), (x + rng.randint(0, sw), y + sh)], fill=colour)
    # near-white sky across the top third (it spans the whole panel width: only the border keeps the
    # rows from being "white"), with clouds, plus a near-white wall patch lower down
    sky_h = max(10, h // 3)
    d.rectangle([0, 0, w, sky_h], fill=(246, 248, 251))
    for _ in range(5):
        cx, cy = rng.randint(0, w), rng.randint(0, sky_h)
        d.ellipse([cx - 60, cy - 18, cx + 60, cy + 18], fill=(252, 252, 253))
    px, py = rng.randint(w // 8, w // 2), rng.randint(sky_h + 10, max(sky_h + 11, h - h // 3))
    d.rectangle([px, py, px + w // 5, py + h // 5], fill=(243, 244, 242))
    return img


def make_page_image(spec: PageSpec) -> Image.Image:
    rng = random.Random(spec.seed)
    page = Image.new("RGB", spec.size, (255, 255, 255))
    for box, (bw, colour) in zip(panel_boxes(spec), spec.borders):
        x0, y0, x1, y1 = box
        panel = _panel_content((x1 - x0, y1 - y0), rng)
        ImageDraw.Draw(panel).rectangle([0, 0, x1 - x0 - 1, y1 - y0 - 1], outline=colour, width=bw)
        page.paste(panel, (x0, y0))
    return page


def _sample(spec: PageSpec, directory: Path, name: str) -> SamplePage:
    directory.mkdir(parents=True, exist_ok=True)
    path = directory / name
    make_page_image(spec).save(path)
    return SamplePage(path=path, spec=spec, boxes=panel_boxes(spec))


@pytest.fixture(scope="session")
def sample_page(tmp_path_factory) -> SamplePage:
    """Synthetic 1024x1536 page with two stacked panels."""
    return _sample(PAGE_2X3, tmp_path_factory.mktemp("sample-pages"), "page-2x3.png")


@pytest.fixture(scope="session")
def sample_45(tmp_path_factory) -> SamplePage:
    """Synthetic 1122x1402 (4:5) page with two stacked panels."""
    return _sample(PAGE_4X5, tmp_path_factory.mktemp("sample-pages-45"), "page-4x5.png")
