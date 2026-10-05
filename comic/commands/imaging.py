"""Imaging commands: split, use, compose, letter, export."""

from __future__ import annotations

import json
import shutil
import sys
from pathlib import Path

from PIL import Image

from comic import fonts
from comic.imaging import compose as compose_mod
from comic.imaging import export as export_mod
from comic.imaging import letter as letter_mod
from comic.imaging import split as split_mod
from comic.project import ComicError, Project, latest_version, next_version


def register(subparsers):
    p = subparsers.add_parser("split", help="cut the current page image into panel images")
    p.add_argument("dir", help="project folder")
    p.add_argument("ep", help="episode id")
    p.add_argument("page", help="page id, e.g. p01")
    p.add_argument("--boxes", help='panel boxes in page pixels, reading order: "x0,y0,x1,y1;x0,y0,x1,y1"')
    p.add_argument("--white", type=int, default=235, help="gutter brightness threshold 0-255 (default 235)")
    p.add_argument("--min-gutter", type=int, default=8, help="minimum gutter thickness in px (default 8)")
    p.set_defaults(func=cmd_split)

    p = subparsers.add_parser("use", help="make an image the current image of a panel")
    p.add_argument("dir", help="project folder")
    p.add_argument("ep", help="episode id")
    p.add_argument("page", help="page id")
    p.add_argument("panel", help="panel id")
    p.add_argument("image", help="image file (e.g. a `comic gen panel` result)")
    p.set_defaults(func=cmd_use)

    p = subparsers.add_parser("compose", help="lay panels out onto the preset canvas")
    p.add_argument("dir", help="project folder")
    p.add_argument("ep", help="episode id")
    p.add_argument("pages", nargs="*", help="page ids (default: every page with panels)")
    p.set_defaults(func=cmd_compose)

    p = subparsers.add_parser("letter", help="draw speech bubbles from lettering.yaml onto composed pages")
    p.add_argument("dir", help="project folder")
    p.add_argument("ep", help="episode id")
    p.add_argument("pages", nargs="*", help="page ids (default: every composed page)")
    p.add_argument("--debug", action="store_true",
                   help="also write lettered/PAGE.debug.png showing slots, bubble_space areas and face boxes")
    p.set_defaults(func=cmd_letter)

    p = subparsers.add_parser("export", help="write upload-ready files and out/preview.html")
    p.add_argument("dir", help="project folder")
    p.add_argument("ep", help="episode id")
    p.set_defaults(func=cmd_export)


# --------------------------------------------------------------------------- helpers


def _open(project_dir, ep_id):
    project = Project.load(project_dir)
    return project, project.episode(ep_id)


def _ep_path(ep, rel) -> Path:
    p = Path(rel)
    return p if p.is_absolute() else ep.root / p


def _ep_rel(ep, path: Path) -> str:
    try:
        return Path(path).resolve().relative_to(ep.root.resolve()).as_posix()
    except ValueError:
        return str(path)


def _load_image(path: Path) -> Image.Image:
    try:
        with Image.open(path) as im:
            return im.convert("RGB")
    except (OSError, ValueError) as e:
        raise ComicError(f"cannot read image {path}: {e}") from e


def _warn(msg: str):
    print(f"warning: {msg}", file=sys.stderr)


def _set_page_state(ep, page_id, **updates):
    state = ep.state()
    ps = state["pages"].setdefault(str(page_id), {})
    if not isinstance(ps, dict):
        ps = state["pages"][str(page_id)] = {}
    for key, value in updates.items():
        if key == "panels":
            panels = ps.get("panels") if isinstance(ps.get("panels"), dict) else {}
            panels = {str(k): v for k, v in panels.items()}
            panels.update({str(k): v for k, v in value.items()})
            ps["panels"] = panels
        else:
            ps[key] = value
    ep.save_state(state)


def current_page_image(ep, page_id) -> Path:
    rel = ep.page_state(page_id).get("page")
    if rel:
        p = _ep_path(ep, rel)
        if not p.is_file():
            raise ComicError(f"state.yaml page image for {page_id} is missing: {p}")
        return p
    latest = latest_version(ep.root / "pages", str(page_id))
    if latest is None:
        raise ComicError(f"no page image for {page_id} yet; generate one: comic gen page {ep.project.root} {ep.id} {page_id}")
    return latest


def current_panels(ep, page_id) -> dict[str, Path]:
    """panel id -> current image path; raises if any panel of the page has none."""
    ids = ep.panel_ids(page_id)
    state_panels = ep.page_state(page_id)["panels"]
    result, missing = {}, []
    for pid in ids:
        rel = state_panels.get(pid)
        p = _ep_path(ep, rel) if rel else None
        if p is None or not p.is_file():
            missing.append(pid)
        else:
            result[pid] = p
    if missing:
        raise ComicError(
            f"{page_id}: no current image for panel(s) {', '.join(missing)}; "
            f"run: comic split {ep.project.root} {ep.id} {page_id} (or comic use … for a single panel)"
        )
    return result


# --------------------------------------------------------------------------- split


def cmd_split(args):
    project, ep = _open(args.dir, args.ep)
    page_id = str(args.page)
    ids = ep.panel_ids(page_id)
    if not ids:
        raise ComicError(f"{page_id} has no panels in script.yaml")
    page_path = current_page_image(ep, page_id)
    img = _load_image(page_path)
    if args.boxes:
        try:
            boxes = split_mod.parse_boxes(args.boxes)
        except ValueError as e:
            raise ComicError(f"--boxes: {e}") from None
        for b in boxes:
            if b[0] < 0 or b[1] < 0 or b[2] > img.width or b[3] > img.height:
                raise ComicError(f"--boxes: box {b} lies outside the page ({img.width}x{img.height})")
    else:
        boxes = split_mod.detect_panels(img, white=args.white, min_gutter=args.min_gutter,
                                        reading_direction=project.reading_direction)
    if len(boxes) != len(ids):
        detected = split_mod.format_boxes(boxes) or "none"
        raise ComicError(
            f"{page_id}: found {len(boxes)} panel(s) in {page_path.name} but script.yaml has {len(ids)} "
            f"({', '.join(ids)}); detected boxes: {detected}; "
            f'give the boxes in reading order: comic split {project.root} {ep.id} {page_id} --boxes "x0,y0,x1,y1;…" '
            f"(or try --white 220 / --min-gutter 4)"
        )
    print(f"page {page_path} ({img.width}x{img.height})")
    written = {}
    panels_dir = ep.root / "panels"
    for pid, box in zip(ids, boxes):
        crop = img.crop(box)
        trimmed = split_mod.trim_border(crop)
        out = next_version(panels_dir, f"{page_id}-{pid}")
        trimmed.save(out)
        written[pid] = _ep_rel(ep, out)
        trim = (crop.width - trimmed.width, crop.height - trimmed.height)
        print(f"panel {pid}: box {','.join(map(str, box))}  {trimmed.width}x{trimmed.height} "
              f"(border trimmed {trim[0]}x{trim[1]} px)  wrote {out}")
    _set_page_state(ep, page_id, panels=written)
    print(f"state: {ep.root / 'state.yaml'} ({page_id} panels set current)")
    print(f"next: comic compose {project.root} {ep.id} {page_id}")


# --------------------------------------------------------------------------- use


def cmd_use(args):
    project, ep = _open(args.dir, args.ep)
    page_id, panel_id = str(args.page), str(args.panel)
    ep.panel(page_id, panel_id)  # validates
    src = Path(args.image).expanduser()
    if not src.is_file():
        for base in (ep.root, project.root):
            if (base / args.image).is_file():
                src = base / args.image
                break
        else:
            raise ComicError(f"image not found: {args.image}")
    src = src.resolve()
    panels_dir = (ep.root / "panels").resolve()
    if src.parent == panels_dir:
        target = src
        print(f"using {target} (already in panels/)")
    else:
        target = next_version(ep.root / "panels", f"{page_id}-{panel_id}")
        if src.suffix.lower() == ".png":
            shutil.copy2(src, target)
        else:
            _load_image(src).save(target)
        print(f"copied {src} -> {target}")
    _set_page_state(ep, page_id, panels={panel_id: _ep_rel(ep, target)})
    print(f"state: {ep.root / 'state.yaml'} ({page_id}-{panel_id} = {_ep_rel(ep, target)})")
    print(f"next: comic compose {project.root} {ep.id} {page_id}")


# --------------------------------------------------------------------------- compose


def _focus(panel: dict) -> float:
    f = panel.get("focus")
    try:
        return 0.5 if f is None else min(1.0, max(0.0, float(f)))
    except (TypeError, ValueError):
        raise ComicError(f"focus must be a number from 0 to 1 (got {f!r})") from None


def cmd_compose(args):
    project, ep = _open(args.dir, args.ep)
    preset = project.preset()
    explicit = bool(args.pages)
    page_ids = [str(p) for p in args.pages] if explicit else ep.page_ids()
    for pid in page_ids:
        ep.page(pid)  # validates
    done = 0
    out_dir = ep.root / "composed"
    for page_id in page_ids:
        try:
            panel_paths = current_panels(ep, page_id)
        except ComicError:
            if explicit:
                raise
            print(f"skip {page_id}: panels not split yet (comic split {project.root} {ep.id} {page_id})")
            continue
        ids = ep.panel_ids(page_id)
        weights = ep.page_slot_weights(page_id)
        focuses = [_focus(ep.panel(page_id, pid)) for pid in ids]
        images = [_load_image(panel_paths[pid]) for pid in ids]
        report: list = []
        try:
            canvas, boxes = compose_mod.compose(images, preset, weights, focuses, report=report)
        except ValueError as e:
            raise ComicError(f"{page_id}: {e}") from None
        out_dir.mkdir(parents=True, exist_ok=True)
        out = out_dir / f"{page_id}.png"
        canvas.save(out)
        layout_path = out_dir / f"{page_id}.layout.json"
        layout_path.write_text(json.dumps(compose_mod.layout_json(canvas.size, ids, boxes)), encoding="utf-8")
        print(f"{page_id}: canvas {canvas.width}x{canvas.height}")
        for pid, box, info, focus in zip(ids, boxes, report, focuses):
            w, h = info["size"]
            pct = info["crop"] * 100
            flag = "  <- heavy crop: regenerate this panel at the slot aspect or set focus" if pct >= 30 else ""
            print(f"  slot {pid}: {w}x{h} at {box[0]},{box[1]}  focus {focus:g}  cropped {pct:.0f}%{flag}")
        print(f"wrote {out}")
        print(f"wrote {layout_path}")
        done += 1
    if not done:
        raise ComicError(f"nothing to compose in {ep.id}; split a page first: comic split {project.root} {ep.id} PAGE")
    print(f"next: comic letter {project.root} {ep.id}")


# --------------------------------------------------------------------------- letter


def _num_pair(value, key, field):
    if (not isinstance(value, (list, tuple)) or len(value) != 2
            or not all(isinstance(v, (int, float)) and not isinstance(v, bool) for v in value)):
        raise ComicError(f"lettering.yaml {key}: {field} must be [x, y] fractions of the panel slot (got {value!r})")
    return float(value[0]), float(value[1])


def _read_layout(path: Path) -> dict:
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
        slots = {str(k): tuple(int(v) for v in box) for k, box in data["slots"].items()}
        return {"canvas": tuple(data["canvas"]), "slots": slots}
    except (OSError, ValueError, KeyError, TypeError) as e:
        raise ComicError(f"cannot read {path}: {e}; re-run comic compose") from None


def _positions(panel, key) -> dict:
    """script.yaml panel `positions`: {character: [x, y]} face centres, fractions of the panel."""
    raw = panel.get("positions")
    if raw in (None, {}):
        return {}
    if not isinstance(raw, dict):
        raise ComicError(f"script.yaml {key}: positions must map character ids to [x, y] (got {raw!r})")
    out = {}
    for name, value in raw.items():
        if (not isinstance(value, (list, tuple)) or len(value) != 2
                or not all(isinstance(v, (int, float)) and not isinstance(v, bool) for v in value)):
            raise ComicError(f"script.yaml {key}: positions.{name} must be [x, y] fractions of the panel "
                             f"(got {value!r})")
        x, y = float(value[0]), float(value[1])
        if not (0 <= x <= 1 and 0 <= y <= 1):
            raise ComicError(f"script.yaml {key}: positions.{name} must be fractions between 0 and 1 (got {value!r})")
        out[str(name)] = (x, y)
    return out


def cmd_letter(args):
    project, ep = _open(args.dir, args.ep)
    lcfg = project.section("lettering")
    explicit = bool(args.pages)
    page_ids = [str(p) for p in args.pages] if explicit else ep.page_ids()
    composed_dir, out_dir = ep.root / "composed", ep.root / "lettered"
    todo = []
    for page_id in page_ids:
        ep.page(page_id)
        src, lay = composed_dir / f"{page_id}.png", composed_dir / f"{page_id}.layout.json"
        if src.is_file() and lay.is_file():
            todo.append((page_id, src, lay))
        elif explicit:
            raise ComicError(f"{page_id} is not composed yet; run: comic compose {project.root} {ep.id} {page_id}")
        else:
            print(f"skip {page_id}: not composed yet")
    if not todo:
        raise ComicError(f"nothing to letter in {ep.id}; run: comic compose {project.root} {ep.id}")

    if lcfg.get("mode") == "in-image":
        for page_id, src, _lay in todo:
            out_dir.mkdir(parents=True, exist_ok=True)
            out = out_dir / f"{page_id}.png"
            shutil.copyfile(src, out)
            print(f"{page_id}: lettering.mode is in-image, copied without overlay; wrote {out}")
        print(f"next: comic export {project.root} {ep.id}")
        return 0

    font_override = lcfg.get("font")
    font_path = fonts.find_font(project.language, project.path(font_override) if font_override else None)
    try:
        base_size = float(lcfg.get("size") or 30)
    except (TypeError, ValueError):
        raise ComicError(f"comic.yaml lettering.size must be a number (got {lcfg.get('size')!r})") from None
    border = project.layout_params()["border"]
    lettering = ep.lettering()
    changed = False
    known_keys = set()
    for page_id, src, lay_path in todo:
        layout = _read_layout(lay_path)
        base = _load_image(src)
        scale = base.width / 1080
        size_px = base_size * scale
        bubbles = []
        dbg_faces, dbg_mouths, dbg_regions = {}, {}, {}
        for panel in ep.panels(page_id):
            pid = str(panel.get("id"))
            positions = _positions(panel, f"{page_id}-{pid}")
            slot = layout["slots"].get(pid)
            if slot is not None:
                sx0, sy0, sx1, sy1 = slot
                sw, sh = sx1 - sx0, sy1 - sy0
                rx0, ry0, rx1, ry1 = letter_mod.bubble_region(panel.get("bubble_space"), sw, sh)
                dbg_regions[pid] = (sx0 + rx0, sy0 + ry0, sx0 + rx1, sy0 + ry1)
                for name, (fx0, fy0, fx1, fy1) in letter_mod.face_boxes(positions, sw, sh).items():
                    dbg_faces[f"{pid}:{name}"] = (sx0 + fx0, sy0 + fy0, sx0 + fx1, sy0 + fy1)
                    mx, my = letter_mod.mouth_point(positions[name], sw, sh)
                    dbg_mouths[f"{pid}:{name}"] = (sx0 + mx, sy0 + my)
            dialogue = [d for d in (panel.get("dialogue") or []) if isinstance(d, dict)]
            if not dialogue:
                continue
            if slot is None:
                raise ComicError(f"{page_id}-{pid} has dialogue but no slot in {lay_path.name}; re-run comic compose")
            sx0, sy0, sx1, sy1 = slot
            sw, sh = sx1 - sx0, sy1 - sy0
            keys = [f"{page_id}-{pid}#{i}" for i in range(len(panel.get("dialogue") or []))]
            entries = [(f"{page_id}-{pid}#{i}", d) for i, d in enumerate(panel.get("dialogue") or []) if isinstance(d, dict)]
            known_keys.update(keys)
            missing = [(k, str(d.get("type") or "speech"), str(d.get("text") or ""), d.get("speaker"))
                       for k, d in entries if not isinstance(lettering.get(k), dict) or "at" not in lettering[k]]
            if missing:
                def measure(kind, text, width_px, spx, _scale=scale):
                    b = letter_mod.Bubble(kind=kind, text=text, at=(0, 0), width=width_px, size=spx)
                    _l, _tw, _th, ex, ey = letter_mod.measure(b, font_path, _scale)
                    return ex, ey
                placed = letter_mod.auto_place(missing, sw, sh, panel.get("bubble_space"), project.reading_direction,
                                               size_px, scale, measure, positions=positions)
                for k, place in placed.items():
                    existing = lettering.get(k) if isinstance(lettering.get(k), dict) else {}
                    lettering[k] = {**place, **existing}  # keep fields the agent already set (size, width, ...)
                changed = True
            for key, d in entries:
                place = lettering[key]
                kind = str(d.get("type") or "speech")
                if kind not in letter_mod.KINDS:
                    raise ComicError(f"{key}: dialogue type '{kind}' must be one of {', '.join(letter_mod.KINDS)}")
                text = str(d.get("text") or "").strip()
                if not text:
                    continue
                ax, ay = _num_pair(place.get("at"), key, "at")
                try:
                    width = float(place.get("width", letter_mod.DEFAULT_WIDTH))
                    size = float(place.get("size", base_size))
                except (TypeError, ValueError):
                    raise ComicError(f"lettering.yaml {key}: width and size must be numbers") from None
                tail = None
                if place.get("tail") is not None:
                    tx, ty = _num_pair(place.get("tail"), key, "tail")
                    tail = (sx0 + tx * sw, sy0 + ty * sh)
                bubbles.append(letter_mod.Bubble(
                    kind=kind, text=text, at=(sx0 + ax * sw, sy0 + ay * sh), width=width * sw,
                    size=size * scale, tail=tail, clamp=bool(place.get("clamp")), slot=slot, key=key,
                ))
        result = letter_mod.render(base, bubbles, font_path, scale=scale, border=border)
        out_dir.mkdir(parents=True, exist_ok=True)
        out = out_dir / f"{page_id}.png"
        result.save(out)
        print(f"{page_id}: {len(bubbles)} bubble(s), font {font_path.name}")
        for b in bubbles:
            if b.info.get("moved"):
                print(f"  {b.key}: clamp moved the bubble by {b.info['moved'][0]},{b.info['moved'][1]} px")
            if b.info.get("tail_skipped"):
                _warn(f"{b.key}: tail target is inside/too close to the bubble; tail skipped "
                      f"- move 'tail' or 'at'")
        for key in sorted(k for k in lettering if k.startswith(f"{page_id}-")):
            if isinstance(lettering[key], dict) and lettering[key].get("auto"):
                print(f"  check {key} (auto)")
        print(f"wrote {out}")
        if getattr(args, "debug", False):
            dbg = letter_mod.debug_overlay(result, layout["slots"], dbg_faces, dbg_mouths, dbg_regions)
            dbg_out = out_dir / f"{page_id}.debug.png"
            dbg.save(dbg_out)
            print(f"wrote {dbg_out} (blue: slots, green: bubble_space, red: face boxes and mouths)")
    stale = sorted(k for k in lettering if any(k.startswith(f"{p}-") for p, _s, _l in todo) and k not in known_keys)
    for k in stale:
        _warn(f"lettering.yaml {k} matches no dialogue line in script.yaml (ignored)")
    if changed:
        path = ep.save_lettering(lettering)
        print(f"wrote {path} (auto placements marked auto: true; edit them, then remove auto)")
    print(f"next: look at the lettered pages, adjust lettering.yaml, re-run; then: comic export {project.root} {ep.id}")


# --------------------------------------------------------------------------- export


def cmd_export(args):
    project, ep = _open(args.dir, args.ep)
    preset = project.preset()
    sources = []
    for page_id in ep.page_ids():
        lettered = ep.root / "lettered" / f"{page_id}.png"
        composed = ep.root / "composed" / f"{page_id}.png"
        if lettered.is_file():
            sources.append(lettered)
        elif composed.is_file():
            _warn(f"{page_id} is not lettered; exporting composed/{page_id}.png")
            sources.append(composed)
        else:
            _warn(f"{page_id} is not composed; skipped")
    if not sources:
        raise ComicError(f"nothing to export in {ep.id}; run: comic compose {project.root} {ep.id}")
    _fmt, ext, _q, _max = export_mod.file_settings(preset)
    out_dir = ep.root / "out"
    images = [_load_image(p) for p in sources]
    width, height = int(preset["width"]), int(preset["height"])
    if preset.get("kind") == "scroll":
        slice_h = int(preset.get("slice_height") or height)
        strip = export_mod.stitch(images, width)
        parts = export_mod.slices(strip, slice_h)
        targets = [out_dir / f"{i + 1:02d}{ext}" for i in range(len(parts))]
        print(f"scroll: {len(images)} page(s) stitched to {strip.width}x{strip.height}, {len(parts)} slice(s) of {slice_h} px")
    else:
        for src, img in zip(sources, images):
            if img.size != (width, height):
                _warn(f"{src.name} is {img.width}x{img.height}, preset canvas is {width}x{height}; re-run comic compose")
        parts = images
        per_post = preset.get("per_post")
        targets = export_mod.slide_targets(out_dir, len(parts), per_post, ext)
        max_pages = preset.get("max_pages")
        per = int(per_post) if per_post else len(parts)
        if max_pages and per > int(max_pages):
            _warn(f"{per} slides per post exceeds the preset max_pages {max_pages}")
    out_dir.mkdir(parents=True, exist_ok=True)
    export_mod.clear_previous(out_dir)
    written = export_mod.write_files(parts, targets, preset)
    for path, nbytes, q in written:
        qnote = f", quality {q}" if ext != ".png" else ""
        print(f"wrote {path} ({nbytes / 1048576:.2f} MB{qnote})")
    files = [p for p, _b, _q in written]
    try:
        from comic.preview import write_preview
    except ImportError:
        _warn("comic.preview is not available; preview.html skipped")
        return 0
    preview = write_preview(out_dir, files, preset, project.title)
    print(f"wrote {preview}")
    print(f"next: open {preview} to check the result")
