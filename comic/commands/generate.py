"""Generation commands: cast new/approve, prompt, gen, import."""

from __future__ import annotations

import re
import shutil
import sys
from pathlib import Path

import yaml
from PIL import Image

from comic import TEMPLATES_DIR
from comic import prompts
from comic.backends import get_backend
from comic.backends.agent import request_path
from comic.project import ComicError, Project, latest_version, next_version, save_yaml, versions

ID_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_-]*$")
KINDS = ("sheet", "page", "panel")


def register(subparsers):
    # cast -----------------------------------------------------------------
    p = subparsers.add_parser("cast", help="create or approve characters")
    cast_sub = p.add_subparsers(dest="cast_command", metavar="ACTION")
    cast_sub.required = True
    c = cast_sub.add_parser("new", help="create cast/ID/ from the character template")
    c.add_argument("dir", help="project folder")
    c.add_argument("id", help="character id (folder name, used in script.yaml)")
    c.add_argument("--name", required=True, help="display name used in prompts")
    c.set_defaults(func=cmd_cast_new)
    c = cast_sub.add_parser("approve", help="copy a sheet draft to refs/sheet.png and mark the character approved")
    c.add_argument("dir", help="project folder")
    c.add_argument("id", help="character id")
    c.add_argument("--draft", help="draft image to approve (default: latest drafts/sheet.vN.png)")
    c.add_argument("--force", action="store_true",
                   help="approve even if appearance is empty or still the template's (warns instead of refusing)")
    c.set_defaults(func=cmd_cast_approve)

    # prompt / gen ---------------------------------------------------------
    for verb, helptext in (("prompt", "assemble and write a prompt file"),
                           ("gen", "generate an image with the configured backend")):
        p = subparsers.add_parser(verb, help=f"{helptext} (sheet | page | panel)")
        sub = p.add_subparsers(dest=f"{verb}_kind", metavar="KIND")
        sub.required = True
        func = cmd_prompt if verb == "prompt" else cmd_gen
        positional = {"sheet": ("dir", "id"), "page": ("dir", "ep", "page"), "panel": ("dir", "ep", "page", "panel")}
        helps = {"sheet": "character sheet", "page": "whole page", "panel": "redraw one panel"}
        for kind in KINDS:
            sp = sub.add_parser(kind, help=helps[kind])
            for name in positional[kind]:
                sp.add_argument(name)
            sp.set_defaults(func=func, kind=kind)
            if verb == "gen":
                sp.add_argument("--fresh", action="store_true",
                                help="re-assemble the prompt even if a prompt file exists")

    # import ---------------------------------------------------------------
    p = subparsers.add_parser(
        "import",
        help="import an externally generated image as the next version",
        description="comic import DIR [EP] (page PAGE | panel PAGE PANEL | sheet ID) IMAGE",
    )
    p.add_argument("dir", help="project folder")
    p.add_argument("target", nargs="+", metavar="ARGS",
                   help="EP page PAGE IMAGE | EP panel PAGE PANEL IMAGE | [EP] sheet ID IMAGE")
    p.set_defaults(func=cmd_import)


# --------------------------------------------------------------------------- helpers


def q(value) -> str:
    s = str(value)
    return f'"{s}"' if (not s or any(ch in s for ch in ' \t&()[]{};\'"')) else s


def _gate(episode) -> None:
    problems = episode.validate()
    if problems:
        for prob in problems:
            print(f"problem: {prob}", file=sys.stderr)
        raise ComicError(
            f"episode {episode.id} has {len(problems)} problem(s) (listed above); fix them before generating"
        )


def _check_image(path: Path) -> None:
    try:
        with Image.open(path) as im:
            im.verify()
    except Exception as e:  # noqa: BLE001 - any decode error means "not an image"
        raise ComicError(f"not a readable image: {path} ({e})") from None


def place_image(src: Path, directory: Path, stem: str) -> Path:
    """Put src into directory as the next stem.vN.png (adopt it if it already is a version there)."""
    src = Path(src).expanduser().resolve()
    if not src.is_file():
        raise ComicError(f"image not found: {src}")
    _check_image(src)
    directory = Path(directory).resolve()
    if src.parent == directory and any(p.resolve() == src for _, p in versions(directory, stem)):
        dest = src
    else:
        dest = next_version(directory, stem)
        if src.suffix.lower() == ".png":
            shutil.copy2(src, dest)
        else:
            with Image.open(src) as im:
                im.save(dest, "PNG")
    req = request_path(dest)
    if req.is_file():
        req.unlink()
    return dest


def _set_current_page(episode, page_id: str, image: Path) -> None:
    state = episode.state()
    ps = state["pages"].get(str(page_id))
    ps = ps if isinstance(ps, dict) else {}
    ps["page"] = image.resolve().relative_to(episode.root.resolve()).as_posix()
    ps["panels"] = {}  # panels belonged to the previous page image
    state["pages"][str(page_id)] = ps
    episode.save_state(state)


# --------------------------------------------------------------------------- cast


def _character_template(cid: str, name: str) -> str:
    template = TEMPLATES_DIR / "character.yaml"
    data = None
    if template.is_file():
        text = template.read_text(encoding="utf-8")
        new = re.sub(r'^id:[ \t]*[^#\n]*?([ \t]+#[^\n]*)?$', lambda m: f"id: {cid}{m.group(1) or ''}",
                     text, count=1, flags=re.M)
        quoted = yaml.safe_dump(name, allow_unicode=True, default_style='"').strip().splitlines()[0]
        new = re.sub(r'^name:[ \t]*("[^"\n]*"|\'[^\'\n]*\'|[^#\n]*?)([ \t]+#[^\n]*)?$',
                     lambda m: f"name: {quoted}{m.group(2) or ''}", new, count=1, flags=re.M)
        try:
            check = yaml.safe_load(new) or {}
        except yaml.YAMLError:
            check = {}
        if check.get("id") == cid and check.get("name") == name:
            return new
        data = yaml.safe_load(text) or {}
    data = data or {"status": "draft", "appearance": "", "personality": "", "height": "",
                    "expressions": list(prompts.DEFAULT_EXPRESSIONS), "inputs": [], "sheet": None}
    data = {"id": cid, "name": name, **{k: v for k, v in data.items() if k not in ("id", "name")}}
    data["status"] = "draft"
    data["sheet"] = None
    return yaml.safe_dump(data, allow_unicode=True, sort_keys=False)


def cmd_cast_new(args):
    project = Project.load(args.dir)
    cid = args.id
    if not ID_RE.match(cid):
        raise ComicError(f"character id '{cid}' must use letters, digits, '-' or '_' (no spaces)")
    root = project.root / "cast" / cid
    yml = root / "character.yaml"
    if yml.exists():
        raise ComicError(f"{yml} already exists (cast new never overwrites it)")
    for sub in ("inputs", "drafts", "refs"):
        (root / sub).mkdir(parents=True, exist_ok=True)
    yml.write_text(_character_template(cid, args.name), encoding="utf-8", newline="\n")
    print(f"wrote {yml}")
    print(f"next: fill in appearance/height/expressions (optional source images in {root / 'inputs'}), "
          f"then: comic gen sheet {q(project.root)} {cid}")


def cmd_cast_approve(args):
    project = Project.load(args.dir)
    ch = project.character(args.id)
    if prompts.appearance_is_placeholder(ch.data.get("appearance")):
        msg = (f"cast/{ch.id}/character.yaml still has the template appearance; "
               "describe the character's look first (or pass --force)")
        if not args.force:
            raise ComicError(msg)
        print(f"warning: {msg.split(';')[0]}", file=sys.stderr)
    drafts = ch.root / "drafts"
    if args.draft:
        cand = Path(args.draft).expanduser()
        if not cand.is_absolute() and not cand.is_file():
            for base in (ch.root, project.root, drafts):
                if (base / cand).is_file():
                    cand = base / cand
                    break
        src = cand.resolve()
        if not src.is_file():
            raise ComicError(f"draft not found: {args.draft}")
    else:
        src = latest_version(drafts, "sheet")
        if src is None:
            raise ComicError(f"no sheet drafts in {drafts}; run: comic gen sheet {q(project.root)} {ch.id}")
    _check_image(src)
    dest = ch.root / "refs" / "sheet.png"
    dest.parent.mkdir(parents=True, exist_ok=True)
    if src.suffix.lower() == ".png":
        shutil.copyfile(src, dest)
    else:
        with Image.open(src) as im:
            im.save(dest, "PNG")
    ch.data["status"] = "approved"
    ch.data["sheet"] = "refs/sheet.png"
    ch.save()
    print(f"approved {ch.id} from {src}")
    print(f"wrote {dest}")
    print(f"wrote {ch.root / 'character.yaml'}")


# --------------------------------------------------------------------------- prompt / gen


def _target(project: Project, args):
    """(kind, context dict) from parsed args."""
    kind = args.kind
    if kind == "sheet":
        return {"character": project.character(args.id)}
    ep = project.episode(args.ep)
    ep.page(args.page)
    ctx = {"episode": ep, "page_id": str(args.page)}
    if kind == "panel":
        ep.panel(args.page, args.panel)
        ctx["panel_id"] = str(args.panel)
    return ctx


def cmd_prompt(args):
    project = Project.load(args.dir)
    ctx = _target(project, args)
    text, refs = prompts.assemble(project, args.kind, **ctx)
    txt, refs_file = prompts.prompt_paths(project, args.kind, **ctx)
    prompts.write_prompt(project, txt, refs_file, text, refs)
    print(f"wrote {txt}")
    print(f"wrote {refs_file}")
    for i, r in enumerate(refs, 1):
        print(f"  image {i}: {r}")
    print(f"next: review/edit the prompt, then: comic gen {args.kind} {' '.join(q(a) for a in _cli_args(project, args))}")


def _cli_args(project: Project, args) -> list[str]:
    if args.kind == "sheet":
        return [str(project.root), args.id]
    out = [str(project.root), args.ep, str(args.page)]
    if args.kind == "panel":
        out.append(str(args.panel))
    return out


def cmd_gen(args):
    project = Project.load(args.dir)
    kind = args.kind
    ctx = _target(project, args)
    if kind in ("page", "panel"):
        _gate(ctx["episode"])

    txt, refs_file = prompts.prompt_paths(project, kind, **ctx)
    if args.fresh or not txt.is_file():
        text, refs = prompts.assemble(project, kind, **ctx)
        prompts.write_prompt(project, txt, refs_file, text, refs)
        print(f"wrote {txt}")
        print(f"wrote {refs_file}")
    else:
        refs = prompts.read_refs(project, refs_file)
        if refs is None:
            _, refs = prompts.assemble(project, kind, **ctx)
            save_yaml(refs_file, [project.rel(p) for p in refs])
            print(f"wrote {refs_file}")
        print(f"using existing prompt {txt} (use --fresh to re-assemble)")
        if kind == "panel":
            current = prompts.current_page_image(ctx["episode"], ctx["page_id"])
            if current is not None and all(r.resolve() != current.resolve() for r in refs):
                print(f"warning: {refs_file.name} does not reference the current page image {current}; "
                      "use --fresh if the panel should match it")

    root = project.root
    if kind == "sheet":
        ch = ctx["character"]
        output = next_version(ch.root / "drafts", "sheet")
        import_cmd = f"comic import {q(root)} sheet {ch.id} {q(output)}"
    else:
        ep, page_id = ctx["episode"], ctx["page_id"]
        if kind == "page":
            output = next_version(ep.root / "pages", page_id)
            import_cmd = f"comic import {q(root)} {ep.id} page {page_id} {q(output)}"
        else:
            output = next_version(ep.root / "panels", f"{page_id}-{ctx['panel_id']}")
            import_cmd = f"comic import {q(root)} {ep.id} panel {page_id} {ctx['panel_id']} {q(output)}"

    backend = get_backend(project)
    print(f"generating {kind} with {backend.name} ({len(refs)} reference image(s)) ...", flush=True)
    image = backend.generate(txt, refs, output, import_command=import_cmd)
    print(f"wrote {image}")
    _after_new_image(project, kind, ctx, image)


def _after_new_image(project: Project, kind: str, ctx: dict, image: Path) -> None:
    root = q(project.root)
    if kind == "sheet":
        ch = ctx["character"]
        print(f"next: look at the sheet; approve with: comic cast approve {root} {ch.id} --draft {q(image)}")
    elif kind == "page":
        ep, page_id = ctx["episode"], ctx["page_id"]
        _set_current_page(ep, page_id, image)
        print(f"wrote {ep.root / 'state.yaml'} (current page {page_id}; panel images cleared)")
        print(f"next: look at the page, then: comic split {root} {ep.id} {page_id}")
    else:
        ep, page_id, panel_id = ctx["episode"], ctx["page_id"], ctx["panel_id"]
        print(f"not current yet; to use it: comic use {root} {ep.id} {page_id} {panel_id} {q(image)}")


# --------------------------------------------------------------------------- import


def _parse_import(args_list: list[str]):
    """-> (ep or None, kind, ids list, image)."""
    rest = list(args_list)
    ep = None
    if rest and rest[0] not in KINDS:
        ep = rest.pop(0)
    usage = "usage: comic import DIR EP page PAGE IMAGE | DIR EP panel PAGE PANEL IMAGE | DIR sheet ID IMAGE"
    if not rest or rest[0] not in KINDS:
        raise ComicError(usage)
    kind = rest.pop(0)
    need = {"sheet": 2, "page": 2, "panel": 3}[kind]
    if len(rest) != need:
        raise ComicError(usage)
    if kind in ("page", "panel") and ep is None:
        raise ComicError(f"import {kind} needs the episode id; {usage}")
    return ep, kind, rest[:-1], rest[-1]


def cmd_import(args):
    project = Project.load(args.dir)
    ep_id, kind, ids, image = _parse_import(args.target)
    src = Path(image).expanduser()
    if not src.is_absolute() and not src.is_file() and (project.root / src).is_file():
        src = project.root / src
    if kind == "sheet":
        ch = project.character(ids[0])
        dest = place_image(src, ch.root / "drafts", "sheet")
        print(f"wrote {dest}")
        _after_new_image(project, kind, {"character": ch}, dest)
        return
    ep = project.episode(ep_id)
    page_id = str(ids[0])
    ep.page(page_id)
    if kind == "page":
        dest = place_image(src, ep.root / "pages", page_id)
        print(f"wrote {dest}")
        _after_new_image(project, kind, {"episode": ep, "page_id": page_id}, dest)
    else:
        panel_id = str(ids[1])
        ep.panel(page_id, panel_id)
        dest = place_image(src, ep.root / "panels", f"{page_id}-{panel_id}")
        print(f"wrote {dest}")
        _after_new_image(project, kind, {"episode": ep, "page_id": page_id, "panel_id": panel_id}, dest)
