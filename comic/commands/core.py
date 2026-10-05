"""Core commands: init, presets, status, doctor."""

from __future__ import annotations

import json
import os
import platform
import re
import shutil
import sys
from pathlib import Path

import yaml

from comic import PRESETS_DIR, TEMPLATES_DIR
from comic import presets as presets_mod
from comic.layout import aspect_text
from comic.project import ComicError, Project

DEFAULT_PRESET = "instagram-carousel"


def register(subparsers):
    p = subparsers.add_parser("init", help="create a comic project folder")
    p.add_argument("dir", help="project folder (created if missing)")
    p.add_argument("--title", help="comic title (default: folder name)")
    p.add_argument("--preset", help=f"format preset (default: {DEFAULT_PRESET}; see `comic presets`)")
    p.set_defaults(func=cmd_init)

    p = subparsers.add_parser("presets", help="list output format presets")
    p.set_defaults(func=cmd_presets)

    p = subparsers.add_parser("status", help="project checklist and next step")
    p.add_argument("dir", help="project folder")
    p.add_argument("ep", nargs="?", help="episode id (default: all episodes)")
    p.set_defaults(func=cmd_status)

    p = subparsers.add_parser("doctor", help="check Python, libraries, fonts and the image relay")
    p.add_argument("dir", nargs="?", help="project folder (optional)")
    p.set_defaults(func=cmd_doctor)


# --------------------------------------------------------------------------- init


def _minimal_config(title: str, preset: str) -> dict:
    return {
        "title": title,
        "language": "ko",
        "reading_direction": "ltr",
        "format": {"preset": preset, "overrides": {}},
        "style": {"description": "", "color": "full colour", "refs": []},
        "lettering": {"mode": "overlay", "font": None, "size": 30},
        "backend": {"name": "codex-relay", "relay": None, "prompt_mode": "Final"},
    }


def _set_scalar(text: str, key: str, value: str, indent: str) -> str:
    """Replace the first `<indent>key: ...` line's value, keeping a trailing comment."""
    pat = re.compile(rf"^({re.escape(indent)}{key}:)[ \t]*(\"[^\"\n]*\"|'[^'\n]*'|[^#\n]*?)([ \t]+#[^\n]*)?$", re.M)
    quoted = json.dumps(value, ensure_ascii=False)
    return pat.sub(lambda m: f"{m.group(1)} {quoted}{m.group(3) or ''}", text, count=1)


def _config_text(title: str, preset: str | None) -> str:
    """comic.yaml text: the template with title/preset filled in (comments kept), or a minimal config."""
    template = TEMPLATES_DIR / "comic.yaml"
    if template.is_file():
        text = template.read_text(encoding="utf-8")
        data = yaml.safe_load(text) or {}
        want_preset = preset or (data.get("format") or {}).get("preset") or DEFAULT_PRESET
        text = _set_scalar(text, "title", title, "")
        text = _set_scalar(text, "preset", want_preset, "  ")
        check = yaml.safe_load(text) or {}
        if check.get("title") == title and (check.get("format") or {}).get("preset") == want_preset:
            return text
        # template layout we could not patch in place: rewrite it as data (comments are lost)
        data["title"] = title
        data.setdefault("format", {})["preset"] = want_preset
        return yaml.safe_dump(data, allow_unicode=True, sort_keys=False)
    return yaml.safe_dump(_minimal_config(title, preset or DEFAULT_PRESET), allow_unicode=True, sort_keys=False)


def cmd_init(args):
    root = Path(args.dir).expanduser().resolve()
    if root.exists() and not root.is_dir():
        raise ComicError(f"{root} exists and is not a folder")
    if args.preset:
        names = presets_mod.preset_names()
        if names and args.preset not in names:
            raise ComicError(f"unknown preset '{args.preset}' (available: {', '.join(names)})")
    for sub in ("cast", "episodes"):
        (root / sub).mkdir(parents=True, exist_ok=True)
    cfg = root / "comic.yaml"
    if cfg.exists():
        print(f"kept existing {cfg} (init never overwrites it)")
    else:
        cfg.write_text(_config_text(args.title or root.name, args.preset), encoding="utf-8")
        print(f"wrote {cfg}")
    print(f"folders: {root / 'cast'}, {root / 'episodes'}")
    print(f"next: edit comic.yaml (style.description), then: comic cast new {root} ID --name NAME")


# --------------------------------------------------------------------------- presets


def cmd_presets(args):
    items = presets_mod.list_presets()
    if not items:
        raise ComicError(f"no presets found in {presets_mod.presets_dir()}")
    for p in items:
        w, h = p.get("width"), p.get("height")
        try:
            size = f"{int(w)}x{int(h)} ({aspect_text(int(w), int(h))})"
        except (TypeError, ValueError):
            size = f"{w}x{h}"
        print(f"{p.get('name')}  {p.get('kind', '?')}  {size}  checked {p.get('checked') or '?'}  {p.get('label') or ''}".rstrip())


# --------------------------------------------------------------------------- status


def _mark(ok: bool) -> str:
    return "[x]" if ok else "[ ]"


def _episode_status(project: Project, ep_id: str, lines: list[str]) -> str | None:
    """Append status lines for one episode; return its next-step hint (None = done)."""
    root = project.root
    ep = project.episode(ep_id)
    problems = ep.validate()
    lines.append(f"episode {ep_id}: {ep.script.get('title') or ''}".rstrip())
    for p in problems:
        lines.append(f"  problem: {p}")
    hint = None
    if problems:
        hint = f"fix the problems in episodes/{ep_id}/script.yaml (or approve the cast), then: comic status {root} {ep_id}"
    lettering = ep.lettering()
    state = ep.state()
    for page in ep.pages():
        pid = str(page.get("id"))
        ps = state["pages"].get(pid) or {}
        page_img = ps.get("page")
        expected = [str(p.get("id")) for p in (page.get("panels") or []) if isinstance(p, dict)]
        current = {str(k): v for k, v in (ps.get("panels") or {}).items()}
        have = [i for i in expected if current.get(i) and (ep.root / current[i]).is_file()]
        composed = (ep.root / "composed" / f"{pid}.png").is_file()
        lettered = (ep.root / "lettered" / f"{pid}.png").is_file()
        auto = [k for k, v in lettering.items() if str(k).startswith(f"{pid}-") and isinstance(v, dict) and v.get("auto")]
        page_ok = bool(page_img) and (ep.root / page_img).is_file()
        lines.append(
            f"  {pid}: {_mark(page_ok)} page {Path(page_img).name if page_ok else '-'}"
            f"  {_mark(len(have) == len(expected))} panels {len(have)}/{len(expected)}"
            f"  {_mark(composed)} composed  {_mark(lettered)} lettered"
            + (f"  ({len(auto)} auto bubble placements to check)" if auto else "")
        )
        if hint is None:
            if not page_ok and len(have) < len(expected):
                hint = f"comic gen page {root} {ep_id} {pid}"
            elif len(have) < len(expected):
                hint = f"comic split {root} {ep_id} {pid}"
            elif not composed:
                hint = f"check each panel against the script, then: comic compose {root} {ep_id}"
            elif not lettered:
                hint = f"comic letter {root} {ep_id}"
            elif auto:
                hint = f"look at lettered/{pid}.png, fix or confirm auto placements in episodes/{ep_id}/lettering.yaml (remove auto: true), then: comic letter {root} {ep_id}"
    out = ep.root / "out"
    exported = sorted(p for p in out.rglob("*") if p.is_file() and p.suffix.lower() in (".jpg", ".jpeg", ".png", ".webp")) if out.is_dir() else []
    preview = (out / "preview.html").is_file()
    lines.append(f"  {_mark(bool(exported))} exported ({len(exported)} files{', preview.html' if preview else ''})")
    if hint is None and not exported:
        hint = f"comic export {root} {ep_id}"
    return hint


def cmd_status(args):
    project = Project.load(args.dir)
    root = project.root
    lines = [f"project: {project.title}  ({root})"]
    try:
        preset = project.preset()
        lines.append(f"format: {preset.get('name')} {preset['width']}x{preset['height']} ({aspect_text(preset['width'], preset['height'])})")
    except ComicError as e:
        lines.append(f"format: problem: {e}")
    hints: list[str] = []
    for p in project.validate():
        lines.append(f"problem: {p}")

    cast = project.characters()
    lines.append("cast:" if cast else "cast: (none)")
    for cid, ch in cast.items():
        lines.append(f"  {_mark(ch.approved)} {cid} ({ch.name}) {'approved' if ch.approved else ch.status}")
    if not cast:
        hints.append(f"comic cast new {root} ID --name NAME")
    unapproved = [cid for cid, ch in cast.items() if not ch.approved]
    if unapproved:
        cid = unapproved[0]
        hints.append(f"make and approve the sheet of {cid}: comic gen sheet {root} {cid}, look at it, then: comic cast approve {root} {cid}")

    ep_ids = [args.ep] if args.ep else project.episode_ids()
    if not ep_ids:
        lines.append("episodes: (none)")
        hints.append(f"write {root / 'episodes' / 'ep01' / 'script.yaml'} (start from templates/script.yaml)")
    for ep_id in ep_ids:
        hint = _episode_status(project, ep_id, lines)
        if hint:
            hints.append(hint)

    for line in lines:
        print(line)
    print(f"next: {hints[0]}" if hints else f"next: done; open the preview: {' '.join(str(project.root / 'episodes' / e / 'out' / 'preview.html') for e in ep_ids)}")


# --------------------------------------------------------------------------- doctor


def cmd_doctor(args):
    results: list[tuple[str, str, str]] = []  # (level, item, detail)

    def add(level, item, detail=""):
        results.append((level, item, detail))

    v = sys.version_info
    add("ok" if v >= (3, 11) else "FAIL", "python", f"{platform.python_version()} ({sys.executable})" + ("" if v >= (3, 11) else "; need 3.11+"))
    try:
        import PIL

        add("ok", "pillow", PIL.__version__)
    except ImportError:
        add("FAIL", "pillow", "not installed: pip install pillow")
    add("ok", "pyyaml", getattr(yaml, "__version__", "?"))

    names = presets_mod.preset_names()
    add("ok" if names else "FAIL", "presets", f"{len(names)} in {PRESETS_DIR}" if names else f"none found in {PRESETS_DIR}")
    tmpl = [n for n in ("comic.yaml", "character.yaml", "script.yaml") if (TEMPLATES_DIR / n).is_file()]
    add("ok" if len(tmpl) == 3 else "WARN", "templates", f"{len(tmpl)}/3 in {TEMPLATES_DIR}")

    project = Project.load(args.dir) if args.dir else None
    language = project.language if project else "ko"
    font_override = None
    if project:
        f = project.section("lettering").get("font")
        font_override = project.path(f) if f else None
        problems = project.validate()
        add("ok" if not problems else "WARN", "project", f"{project.root}" + (f"; {len(problems)} problems (see comic status)" if problems else ""))
    from comic.fonts import find_font

    try:
        font = find_font(language, font_override)
        level = "WARN" if font_override and Path(font) != Path(font_override) else "ok"
        add(level, "font", f"{font} (language {language})" + ("; lettering.font not found, using this instead" if level == "WARN" else ""))
    except ComicError as e:
        add("FAIL" if project else "WARN", "font", str(e))

    backend = project.section("backend") if project else {}
    backend_name = backend.get("name", "codex-relay") if project else None
    if backend_name in (None, "codex-relay"):
        relay = backend.get("relay")
        source = "backend.relay"
        if relay:
            relay_path = project.path(relay)
        elif os.environ.get("COMIC_RELAY"):
            relay_path, source = Path(os.environ["COMIC_RELAY"]).expanduser(), "env COMIC_RELAY"
        else:
            relay_path = None
        need = "FAIL" if project else "WARN"
        if relay_path is None:
            add(need, "relay", "not set: set backend.relay in comic.yaml or env COMIC_RELAY to imagen.ps1 "
                "(https://github.com/Saeyeon-developer/codex-image-relay), or use backend.name: agent")
        elif relay_path.is_file():
            add("ok", "relay", f"{relay_path} ({source})")
        else:
            add(need, "relay", f"{relay_path} ({source}) does not exist")
        ps = shutil.which("powershell") or shutil.which("pwsh")
        add("ok" if ps else need, "powershell", ps or "not found on PATH (the codex relay needs it)")
    else:
        add("ok", "backend", f"{backend_name} (no relay needed)")

    for level, item, detail in results:
        print(f"{level:<4} {item}: {detail}".rstrip())
    failed = [r for r in results if r[0] == "FAIL"]
    print("doctor: " + ("all good" if not failed else f"{len(failed)} problem(s) to fix"))
    return 1 if failed else 0
