"""Tests for the imaging module: split, use, compose, letter, export (no image generator involved)."""

from __future__ import annotations

import json
import math
import shutil
from pathlib import Path

import pytest
import yaml
from PIL import Image, ImageChops, ImageDraw

from comic import cli
from comic import fonts
from comic.imaging import compose as compose_mod
from comic.imaging import export as export_mod
from comic.imaging import letter as letter_mod
from comic.imaging import split as split_mod
from comic.layout import slot_boxes
from comic.project import ComicError, save_yaml



def _font_available() -> bool:
    try:
        fonts.find_font("ko")
        return True
    except ComicError:
        return False


def make_project(root: Path, page, preset="instagram-carousel", overrides=None, reading_direction="ltr") -> Path:
    root.mkdir(parents=True, exist_ok=True)
    save_yaml(root / "comic.yaml", {
        "title": "Crepe Day", "language": "ko", "reading_direction": reading_direction,
        "format": {"preset": preset, "overrides": overrides or {}},
        "style": {"description": "clean line art", "color": "full colour", "refs": []},
        "lettering": {"mode": "overlay", "font": None, "size": 30},
        "backend": {"name": "agent"},
    })
    cdir = root / "cast" / "alice"
    (cdir / "refs").mkdir(parents=True)
    Image.new("RGB", (60, 40), "gray").save(cdir / "refs" / "sheet.png")
    save_yaml(cdir / "character.yaml", {"id": "alice", "name": "Alice", "status": "approved", "appearance": "x",
                                        "inputs": [], "sheet": "refs/sheet.png"})
    ep = root / "episodes" / "ep01"
    ep.mkdir(parents=True)
    save_yaml(ep / "script.yaml", {"episode": "ep01", "title": "t", "pages": [{"id": "p01", "panels": [
        {"id": 1, "weight": 55, "characters": ["alice"], "bubble_space": "top-left", "dialogue": [
            {"type": "narration", "text": "토요일 오후, 역 앞 상점가"},
            {"type": "speech", "speaker": "alice", "text": "앨리스! 저기 크레페 가게 새로 생겼대!"},
        ]},
        {"id": 2, "weight": 45, "focus": 0.3, "characters": ["alice"], "bubble_space": "top right", "dialogue": [
            {"type": "thought", "speaker": "alice", "text": "…맛있어."},
            {"type": "shout", "speaker": "alice", "text": "거봐, 맛있지?!"},
        ]},
    ]}]})
    (ep / "pages").mkdir()
    shutil.copy(page.path, ep / "pages" / "p01.v1.png")
    save_yaml(ep / "state.yaml", {"pages": {"p01": {"page": "pages/p01.v1.png"}}})
    return root


def run(*argv) -> int:
    return cli.main([str(a) for a in argv])


# --------------------------------------------------------------------------- unit


def test_detect_panels_sample_pages(sample_page, sample_45):
    # white-ish "sky" regions inside the panels must not split them; borders and gutters decide
    assert sample_page.size == (1024, 1536) and sample_45.size == (1122, 1402)
    for page in (sample_page, sample_45):
        img = Image.open(page.path).convert("RGB")
        assert img.size == page.size
        boxes = split_mod.detect_panels(img)
        assert boxes == page.boxes and len(boxes) == 2
        assert boxes[1][1] - boxes[0][3] == page.spec.gutter  # the gutter, not an inner white band
        # the drawn (gray, not pure black) borders are found and trimmed on every side
        for box, (bw, _colour) in zip(boxes, page.spec.borders):
            crop = img.crop(box)
            assert split_mod.border_widths(crop) == {"top": bw, "bottom": bw, "left": bw, "right": bw}
            assert split_mod.trim_border(crop).size == (crop.width - 2 * bw, crop.height - 2 * bw)


def _grid_page(w=600, h=400):
    img = Image.new("RGB", (w, h), "white")
    d = ImageDraw.Draw(img)
    boxes = [(20, 20, 290, 380), (310, 20, 580, 380)]
    for i, b in enumerate(boxes):
        d.rectangle(b, fill=(180, 120 + 40 * i, 90), outline=(70, 70, 70), width=2)
    return img, boxes


def test_reading_direction_orders_side_by_side_panels():
    img, _ = _grid_page()
    ltr = split_mod.detect_panels(img, reading_direction="ltr")
    rtl = split_mod.detect_panels(img, reading_direction="rtl")
    assert len(ltr) == 2 and ltr[0][0] < ltr[1][0]
    assert rtl == list(reversed(ltr))


def test_trim_border_removes_gray_border():
    img = Image.new("RGB", (200, 150), (230, 200, 170))
    d = ImageDraw.Draw(img)
    d.rectangle((0, 0, 199, 149), outline=(90, 90, 90), width=2)  # gray 2 px border
    out = split_mod.trim_border(img)
    assert out.size in ((196, 146), (194, 144))
    assert out.convert("L").getextrema()[0] > 150
    plain = Image.new("RGB", (200, 150), (230, 200, 170))
    assert split_mod.trim_border(plain).size == (200, 150)


def test_compose_canvas_and_crop_report():
    preset = {"width": 1080, "height": 1350, "layout": {"margin": 36, "gutter": 24, "border": 4}}
    panels = [Image.new("RGB", (1000, 800), "red"), Image.new("RGB", (2000, 800), "blue")]
    report = []
    canvas, boxes = compose_mod.compose(panels, preset, [55, 45], [0.5, 0.5], report=report)
    assert canvas.size == (1080, 1350)
    assert boxes == slot_boxes(1080, 1350, [55, 45], 36, 24)
    assert len(report) == 2 and all(0 <= r["crop"] < 1 for r in report)
    assert canvas.getpixel((36, boxes[0][1] + 50)) == (0, 0, 0)  # border


def test_wrap_balanced_even_lines():
    if not _font_available():
        pytest.skip("no font")
    font = letter_mod.load_font(str(fonts.find_font("ko")), 30, 600)
    text = "this is a fairly long sentence that should wrap into lines of similar length ok"
    lines = letter_mod.wrap_balanced(text, font, 300)
    widths = [font.getlength(line) for line in lines]
    assert all(w <= 300 for w in widths)
    assert len(lines) >= 2 and max(widths) - min(widths) < 120
    # a single chunk wider than the width is broken by characters
    long = letter_mod.wrap_balanced("가나다라마바사아자차카타파하가나다라마바사아자차카타파하", font, 200)
    assert len(long) >= 2 and all(font.getlength(line) <= 200 for line in long)


def test_parse_bubble_space():
    assert letter_mod.parse_bubble_space("top-left") == ("top", "left")
    assert letter_mod.parse_bubble_space("lower right corner") == ("bottom", "right")
    assert letter_mod.parse_bubble_space("bottom centre") == ("bottom", "centre")
    assert letter_mod.parse_bubble_space("sky") == ("top", None)


def test_encode_lowers_quality_then_fails():
    img = Image.effect_noise((600, 600), 80).convert("RGB")
    data, q = export_mod.encode(img, "JPEG", 92, 10_000_000)
    assert q == 92
    full = len(export_mod.encode(img, "JPEG", 92, None)[0])
    data, q = export_mod.encode(img, "JPEG", 92, full * 0.8)
    assert q < 92 and len(data) <= full * 0.8
    with pytest.raises(ComicError):
        export_mod.encode(img, "JPEG", 92, 1000)


# --------------------------------------------------------------------------- pipeline


def test_pipeline_split_compose_letter_export(tmp_path, capsys, sample_page):
    if not _font_available():
        pytest.skip("no font")
    root = make_project(tmp_path / "proj", sample_page)
    ep = root / "episodes" / "ep01"

    assert run("split", root, "ep01", "p01") == 0
    assert (ep / "panels" / "p01-1.v1.png").is_file() and (ep / "panels" / "p01-2.v1.png").is_file()
    state = yaml.safe_load((ep / "state.yaml").read_text(encoding="utf-8"))
    assert state["pages"]["p01"]["panels"] == {"1": "panels/p01-1.v1.png", "2": "panels/p01-2.v1.png"}
    assert state["pages"]["p01"]["page"] == "pages/p01.v1.png"
    p1 = Image.open(ep / "panels" / "p01-1.v1.png")
    assert p1.width < sample_page.boxes[0][2] - sample_page.boxes[0][0]  # border trimmed

    assert run("compose", root, "ep01") == 0
    composed = Image.open(ep / "composed" / "p01.png")
    assert composed.size == (1080, 1350)
    layout = json.loads((ep / "composed" / "p01.layout.json").read_text(encoding="utf-8"))
    assert layout == {"canvas": [1080, 1350], "slots": {k: list(v) for k, v in zip(
        ("1", "2"), slot_boxes(1080, 1350, [55, 45], 36, 24))}}
    out = capsys.readouterr().out
    assert "cropped" in out

    # one manual placement must survive; the rest are auto-placed
    save_yaml(ep / "lettering.yaml", {"p01-1#0": {"at": [0.17, 0.06], "width": 0.30}})
    assert run("letter", root, "ep01") == 0
    lettered = Image.open(ep / "lettered" / "p01.png")
    assert lettered.size == composed.size
    assert ImageChops.difference(lettered.convert("RGB"), composed.convert("RGB")).getbbox() is not None
    lettering = yaml.safe_load((ep / "lettering.yaml").read_text(encoding="utf-8"))
    assert lettering["p01-1#0"] == {"at": [0.17, 0.06], "width": 0.30}
    for key in ("p01-1#1", "p01-2#0", "p01-2#1"):
        assert lettering[key]["auto"] is True
        x, y = lettering[key]["at"]
        assert 0 <= x <= 1 and 0 <= y <= 1
    # bubble_space top right -> panel 2 bubbles on the right half, top half
    assert all(lettering[k]["at"][0] > 0.4 and lettering[k]["at"][1] < 0.5 for k in ("p01-2#0", "p01-2#1"))
    out = capsys.readouterr().out
    assert "check p01-1#1 (auto)" in out and "check p01-1#0" not in out

    # re-running keeps the auto placements as they are
    before = (ep / "lettering.yaml").read_text(encoding="utf-8")
    assert run("letter", root, "ep01", "p01") == 0
    assert (ep / "lettering.yaml").read_text(encoding="utf-8") == before

    assert run("export", root, "ep01") == 0
    outdir = ep / "out"
    assert (outdir / "01.jpg").is_file() and (outdir / "preview.html").is_file()
    assert Image.open(outdir / "01.jpg").size == (1080, 1350)
    assert sorted(p.name for p in outdir.iterdir()) == ["01.jpg", "preview.html"]


def test_split_count_mismatch_and_boxes(tmp_path, capsys, sample_page):
    root = make_project(tmp_path / "proj", sample_page)
    ep = root / "episodes" / "ep01"
    script = yaml.safe_load((ep / "script.yaml").read_text(encoding="utf-8"))
    script["pages"][0]["panels"].append({"id": 3, "characters": ["alice"]})
    save_yaml(ep / "script.yaml", script)
    assert run("split", root, "ep01", "p01") == 1
    err = capsys.readouterr().err
    first, second = sample_page.boxes
    assert "--boxes" in err and ",".join(str(v) for v in first) in err
    mid = (first[1] + first[3]) // 2
    boxes = [(first[0], first[1], first[2], mid), (first[0], mid, first[2], first[3]), second]
    assert run("split", root, "ep01", "p01", "--boxes", ";".join(",".join(str(v) for v in b) for b in boxes)) == 0
    assert (ep / "panels" / "p01-3.v1.png").is_file()


def test_use_copies_external_image_as_next_version(tmp_path, sample_page):
    root = make_project(tmp_path / "proj", sample_page)
    ep = root / "episodes" / "ep01"
    assert run("split", root, "ep01", "p01") == 0
    ext = tmp_path / "regen.jpg"
    Image.new("RGB", (240, 100), "green").save(ext)
    assert run("use", root, "ep01", "p01", "2", ext) == 0
    assert (ep / "panels" / "p01-2.v2.png").is_file()
    state = yaml.safe_load((ep / "state.yaml").read_text(encoding="utf-8"))
    assert state["pages"]["p01"]["panels"]["2"] == "panels/p01-2.v2.png"
    # an image already in panels/ is used in place
    assert run("use", root, "ep01", "p01", "2", ep / "panels" / "p01-2.v1.png") == 0
    state = yaml.safe_load((ep / "state.yaml").read_text(encoding="utf-8"))
    assert state["pages"]["p01"]["panels"]["2"] == "panels/p01-2.v1.png"
    assert not (ep / "panels" / "p01-2.v3.png").exists()


def test_export_per_post_and_scroll(tmp_path, sample_page):
    root = make_project(tmp_path / "proj", sample_page, overrides={"per_post": 1})
    ep = root / "episodes" / "ep01"
    assert run("split", root, "ep01", "p01") == 0
    assert run("compose", root, "ep01") == 0
    assert run("export", root, "ep01") == 0  # not lettered: falls back to composed
    assert (ep / "out" / "post-1" / "01.jpg").is_file()

    root2 = make_project(tmp_path / "scroll", sample_page, preset="vertical-scroll", overrides={"slice_height": 500})
    ep2 = root2 / "episodes" / "ep01"
    assert run("split", root2, "ep01", "p01") == 0
    assert run("compose", root2, "ep01") == 0
    assert run("export", root2, "ep01") == 0
    files = sorted(p.name for p in (ep2 / "out").glob("*.jpg"))
    assert files == ["01.jpg", "02.jpg", "03.jpg"]  # 1280 px page -> 500 + 500 + 280
    assert Image.open(ep2 / "out" / "03.jpg").size == (800, 280)


def test_clamp_keeps_bubble_inside_slot():
    if not _font_available():
        pytest.skip("no font")
    base = Image.new("RGB", (1080, 1350), "white")
    slot = (36, 36, 1044, 600)
    b = letter_mod.Bubble(kind="shout", text="거봐, 맛있지?!", at=(1040, 40), width=210, size=26,
                          clamp=True, slot=slot, key="k")
    out = letter_mod.render(base, [b], fonts.find_font("ko"), scale=1.0)
    assert b.info.get("moved")
    # nothing drawn outside the slot
    outside = out.crop((1044, 0, 1080, 1350))
    assert outside.convert("L").getextrema()[0] == 255


# --------------------------------------------------------------------------- tails and positions


def _ellipse_mask(size, cx, cy, a, b):
    mask = Image.new("L", size, 0)
    ImageDraw.Draw(mask).ellipse([cx - a, cy - b, cx + a, cy + b], fill=255)
    return mask


def _thought_case(tail):
    """The p02-1#1 bubble of the crepe experiment: clamp moves it left onto its own tail target."""
    slot = (36, 36, 1044, 663)
    sw, sh = slot[2] - slot[0], slot[3] - slot[1]
    tip = None if tail is None else (slot[0] + tail[0] * sw, slot[1] + tail[1] * sh)
    return letter_mod.Bubble(kind="thought", text="…내 것도 골라줬다.", at=(slot[0] + 0.905 * sw, slot[1] + 0.36 * sh),
                             width=0.12 * sw, size=30, tail=tip, clamp=True, slot=slot, key="p02-1#1")


def _render_pair(tail):
    font_path = fonts.find_font("ko")
    base = Image.new("RGB", (1080, 1350), (120, 160, 200))
    with_tail, without = _thought_case(tail), _thought_case(None)
    img_t = letter_mod.render(base, [with_tail], font_path, scale=1.0)
    img_n = letter_mod.render(base, [without], font_path, scale=1.0)
    _l, tw, th, _ex, _ey = letter_mod.measure(without, font_path, 1.0)
    a, b = letter_mod.shape_axes("thought", tw, th, 1.0)
    dx, dy = without.info.get("moved", (0, 0))
    cx, cy = without.at[0] + dx, without.at[1] + dy
    return with_tail, img_t, img_n, (cx, cy, a, b)


def test_thought_tail_inside_bubble_is_skipped():
    if not _font_available():
        pytest.skip("no font")
    bubble, img_t, img_n, (cx, cy, a, b) = _render_pair((0.80, 0.33))
    assert bubble.info.get("moved") and bubble.info.get("tail_skipped")
    inside = _ellipse_mask(img_t.size, cx, cy, a * 0.95, b * 0.95)
    diff = ImageChops.difference(img_t, img_n).convert("L")
    assert ImageChops.multiply(diff, inside).getbbox() is None  # no tail circles inside the cloud
    assert diff.getbbox() is None


def test_thought_tail_outside_is_drawn_outside_body():
    if not _font_available():
        pytest.skip("no font")
    bubble, img_t, img_n, (cx, cy, a, b) = _render_pair((0.70, 0.55))
    assert not bubble.info.get("tail_skipped")
    diff = ImageChops.difference(img_t, img_n).convert("L").point(lambda v: 255 if v > 8 else 0)
    inside = _ellipse_mask(img_t.size, cx, cy, a * 0.95, b * 0.95)
    assert ImageChops.multiply(diff, inside).getbbox() is None
    assert diff.getbbox() is not None  # the circles were drawn, outside the body


def test_speech_tail_target_too_close_is_skipped():
    if not _font_available():
        pytest.skip("no font")
    font_path = fonts.find_font("ko")
    probe = letter_mod.Bubble(kind="speech", text="응?", at=(500, 300), width=200, size=30)
    _l, _tw, _th, ex, _ey = letter_mod.measure(probe, font_path, 1.0)
    near = letter_mod.Bubble(kind="speech", text="응?", at=(500, 300), width=200, size=30, tail=(500 + ex + 5, 300))
    far = letter_mod.Bubble(kind="speech", text="응?", at=(500, 300), width=200, size=30, tail=(500 + ex + 80, 300))
    letter_mod.render(Image.new("RGB", (1080, 1350), "white"), [near, far], font_path)
    assert near.info.get("tail_skipped") and not far.info.get("tail_skipped")


def _fake_measure(kind, text, width_px, size_px):
    return width_px / 2 * 1.32 + 20, 45.0


def test_auto_place_with_positions_avoids_faces_and_aims_tails():
    sw, sh = 1008, 627
    positions = {"alice": (0.39, 0.29), "bob": (0.68, 0.51), "clerk": (0.04, 0.25)}
    entries = [("p02-1#0", "speech", "딸기 생크림 두 개요!", "alice"),
               ("p02-1#1", "thought", "…내 것도 골라줬다.", "bob"),
               ("p02-1#2", "narration", "그날 오후", None)]
    placed = letter_mod.auto_place(entries, sw, sh, "top-right", "ltr", 30, 1.0, _fake_measure, positions=positions)
    faces = letter_mod.face_boxes(positions, sw, sh)
    for key, kind, text, speaker in entries:
        p = placed[key]
        assert p["auto"] is True
        cx, cy = p["at"][0] * sw, p["at"][1] * sh
        ex, ey = _fake_measure(kind, text, p["width"] * sw, 30)
        rect = (cx - ex, cy - ey, cx + ex, cy + ey)
        assert rect[0] >= 0 and rect[1] >= 0 and rect[2] <= sw and rect[3] <= sh
        for box in faces.values():
            assert letter_mod._overlap(rect, box) == 0, (key, rect, box)
        if kind == "narration":
            assert "tail" not in p
        else:
            mx, my = letter_mod.mouth_point(positions[speaker], sw, sh)
            tx, ty = p["tail"][0] * sw, p["tail"][1] * sh
            # the tail ends near the speaker's mouth, closer to it than to anyone else's
            d_own = math.hypot(tx - mx, ty - my)
            assert d_own <= letter_mod.TAIL_SHORT * sh + 2
            for other, pos in positions.items():
                if other != speaker:
                    ox, oy = letter_mod.mouth_point(pos, sw, sh)
                    assert d_own < math.hypot(tx - ox, ty - oy)
    # without positions the old row packing is used (same result as before)
    old = letter_mod.auto_place([e[:3] for e in entries], sw, sh, "top-right", "ltr", 30, 1.0, _fake_measure)
    assert old == letter_mod.auto_place(entries, sw, sh, "top-right", "ltr", 30, 1.0, _fake_measure, positions={})


def test_letter_positions_debug_and_tail_warning(tmp_path, capsys, sample_page):
    if not _font_available():
        pytest.skip("no font")
    root = make_project(tmp_path / "proj", sample_page)
    ep = root / "episodes" / "ep01"
    script = yaml.safe_load((ep / "script.yaml").read_text(encoding="utf-8"))
    script["pages"][0]["panels"][0]["positions"] = {"alice": [0.5, 0.4]}
    save_yaml(ep / "script.yaml", script)
    assert run("split", root, "ep01", "p01") == 0
    assert run("compose", root, "ep01") == 0
    # a manual tail that ends inside its own (clamped) bubble
    save_yaml(ep / "lettering.yaml", {"p01-2#0": {"at": [0.97, 0.3], "width": 0.12, "tail": [0.9, 0.3], "clamp": True}})
    capsys.readouterr()
    assert run("letter", root, "ep01", "--debug") == 0
    captured = capsys.readouterr()
    assert "p01-2#0: tail target is inside/too close to the bubble; tail skipped" in captured.err
    assert "check p01-1#1 (auto)" in captured.out
    assert (ep / "lettered" / "p01.debug.png").is_file()
    lettering = yaml.safe_load((ep / "lettering.yaml").read_text(encoding="utf-8"))
    p = lettering["p01-1#1"]
    assert p["auto"] is True and "tail" in p
    # the speech bubble stays off alice's face box
    slot = json.loads((ep / "composed" / "p01.layout.json").read_text(encoding="utf-8"))["slots"]["1"]
    sw, sh = slot[2] - slot[0], slot[3] - slot[1]
    box = letter_mod.face_boxes({"alice": (0.5, 0.4)}, sw, sh)["alice"]
    cx, cy = p["at"][0] * sw, p["at"][1] * sh
    assert not (box[0] < cx < box[2] and box[1] < cy < box[3])

    script["pages"][0]["panels"][0]["positions"] = {"alice": [0.5]}
    save_yaml(ep / "script.yaml", script)
    assert run("letter", root, "ep01") == 1
    assert "positions.alice" in capsys.readouterr().err
