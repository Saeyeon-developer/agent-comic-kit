"""Prompt assembly for character sheets, pages and single panels.

Every prompt keeps the same structure (it is what worked in testing):
purpose line with the exact aspect ratio -> numbered reference roles ->
characters with fixed appearance and height relation -> layout rules ->
per-panel blocks -> style -> text rule.

Style comes only from comic.yaml style.description / style.color. The fixed
template text never names a genre.
"""

from __future__ import annotations

from pathlib import Path

from comic.layout import aspect_text, slot_boxes
from comic.project import ComicError, Project, latest_version, load_yaml, save_yaml

SHEET_ASPECT = (3, 2)
DEFAULT_EXPRESSIONS = ["neutral", "smile", "surprised", "annoyed"]

SHEET_REF_ROLE = (
    "keep face, hair, outfit, accessories and body type exactly; "
    "do not copy the sheet's background, layout or expression grid"
)
STYLE_REF_ROLE = (
    "style reference: match line work, colouring and lighting only; "
    "do not copy its characters, layout or composition"
)
BUBBLE_KINDS = {
    "speech": "speech bubble (round, with a tail to {who})",
    "shout": "shout bubble (spiky burst outline, with a tail to {who})",
    "thought": "thought bubble (cloud shape with small trailing circles toward {who})",
    "narration": "narration box (rectangular caption box)",
}


# --------------------------------------------------------------------------- small helpers


def shape_words(w: float, h: float) -> str:
    """Plain words for an aspect ratio, e.g. 'slightly taller than wide'."""
    r = w / h
    if abs(r - 1) < 0.02:
        return "square"
    big, small, longer = (r, 1 / r, "wider than tall") if r > 1 else (1 / r, r, "taller than wide")
    if big < 1.34:
        return f"slightly {longer}"
    if big < 2:
        return longer
    return f"much {longer}"


def ratio_line(w: float, h: float) -> str:
    return f"The whole image is exactly {aspect_text(w, h)} ({shape_words(w, h)})."


def _clean(text) -> str:
    return " ".join(str(text or "").split())


def _sentence(text) -> str:
    t = _clean(text)
    if t and t[-1] not in ".!?":
        t += "."
    return t


def _style_lines(project: Project) -> list[str]:
    style = project.section("style")
    desc = _clean(style.get("description"))
    color = _clean(style.get("color"))
    lines = []
    if desc:
        lines.append(_sentence(desc[0].upper() + desc[1:]))
    if color and color.lower() not in desc.lower():
        lines.append(_sentence(f"Colour: {color}"))
    return lines


def _style_refs(project: Project) -> list[Path]:
    refs = []
    for r in project.section("style").get("refs") or []:
        p = project.path(r)
        if not p.is_file():
            raise ComicError(f"comic.yaml: style ref not found: {r}")
        refs.append(p)
    return refs


def _lettering_mode(project: Project) -> str:
    return str(project.section("lettering").get("mode") or "overlay")


def _characters(project: Project, ids) -> list:
    return [project.character(cid) for cid in ids]


def _character_block(chars) -> list[str]:
    if not chars:
        return []
    lines = ["[Characters — keep their appearance exactly]"]
    for ch in chars:
        appearance = _clean(ch.data.get("appearance"))
        lines.append(f"{ch.name}: {_sentence(appearance)}" if appearance else f"{ch.name}: as in the character sheet.")
    heights = [_sentence(f"{ch.name} — {_clean(ch.data.get('height'))}") for ch in chars if _clean(ch.data.get("height"))]
    if len(chars) > 1 and heights:
        lines.append("Heights (keep these differences in every panel): " + " ".join(heights))
    return lines


def _sheet_refs(project: Project, chars, where: str) -> list[tuple[Path, str]]:
    """(sheet path, role line) for each character; ComicError if a sheet is missing."""
    out = []
    for ch in chars:
        sheet = ch.sheet_path()
        if not ch.approved or sheet is None:
            raise ComicError(
                f"{where}: character '{ch.id}' has no approved sheet; run: comic gen sheet {project.root} {ch.id}, "
                f"then: comic cast approve {project.root} {ch.id}"
            )
        out.append((sheet, f"character sheet of {ch.name} — {SHEET_REF_ROLE}."))
    return out


def _ref_lines(roles: list[str], label: str = "Image") -> list[str]:
    if not roles:
        return []
    return ["[Reference images]"] + [f"{label} {i}: {role}" for i, role in enumerate(roles, 1)]


def _panel_block(project: Project, panel: dict, title: str, chars) -> list[str]:
    shot = _clean(panel.get("shot"))
    lines = [f"[{title}{' — ' + shot if shot else ''}]"]
    if chars:
        lines.append("Characters in this panel: " + ", ".join(ch.name for ch in chars) + ".")
    else:
        lines.append("No characters in this panel.")
    if _clean(panel.get("action")):
        lines.append(_sentence(panel.get("action")))
    if _clean(panel.get("background")):
        lines.append(_sentence(f"Background: {_clean(panel.get('background'))}"))
    space = _clean(panel.get("bubble_space"))
    if _lettering_mode(project) == "in-image":
        lines += _dialogue_lines(project, panel)
    elif space:
        lines.append(f"Keep the {space} area simple and empty for speech bubbles.")
    return lines


def _dialogue_lines(project: Project, panel: dict) -> list[str]:
    dialogue = panel.get("dialogue") or []
    if not dialogue:
        return []
    names = {cid: ch.name for cid, ch in project.characters().items()}
    lines = ["Lettering in this panel, in reading order:"]
    for line in dialogue:
        if not isinstance(line, dict):
            continue
        kind = str(line.get("type") or "speech")
        who = names.get(str(line.get("speaker")), _clean(line.get("speaker")) or "the speaker")
        shape = BUBBLE_KINDS.get(kind, BUBBLE_KINDS["speech"]).format(who=who)
        text = _clean(line.get("text")).replace('"', "'")
        lines.append(f'- {shape}: "{text}"')
    space = _clean(panel.get("bubble_space"))
    if space:
        lines.append(f"Place the bubbles in the {space} area, without covering faces.")
    return lines


def _text_rule(project: Project) -> str:
    if _lettering_mode(project) == "in-image":
        return ("Draw the bubbles and boxes listed above with exactly that text, clearly readable. "
                "No other text, sound effects, readable signs or watermarks.")
    return "No speech bubbles, text, sound effects, readable signs or watermarks."


# --------------------------------------------------------------------------- sheet


def sheet_prompt(project: Project, character) -> tuple[str, list[Path]]:
    ch = character
    refs: list[Path] = []
    roles: list[str] = []
    for inp in ch.data.get("inputs") or []:
        p = Path(inp)
        p = p if p.is_absolute() else ch.root / p
        if not p.is_file():
            raise ComicError(f"cast/{ch.id}/character.yaml: input image not found: {inp}")
        refs.append(p)
        roles.append(f"{ch.name} — carry over the design only (face, hair, outfit, colours, accessories, body type), "
                     "not its render style, background or pose.")

    expressions = [_clean(e) for e in (ch.data.get("expressions") or []) if _clean(e)]
    for d in DEFAULT_EXPRESSIONS:
        if len(expressions) >= 4:
            break
        if d not in expressions:
            expressions.append(d)
    expressions = expressions[:4]

    lines = [f"One character design sheet for {ch.name}. {ratio_line(*SHEET_ASPECT)}", ""]
    if roles:
        lines += _ref_lines(roles, "Source image") + [""]
    lines += ["[Character — keep exactly]"]
    appearance = _clean(ch.data.get("appearance"))
    lines.append(f"{ch.name}: {_sentence(appearance)}" if appearance else f"{ch.name}: as in the source images.")
    if _clean(ch.data.get("height")):
        lines.append(_sentence(f"Height: {_clean(ch.data.get('height'))}"))
    if _clean(ch.data.get("personality")):
        lines.append(_sentence(f"Personality (show it in the expressions): {_clean(ch.data.get('personality'))}"))
    lines += [
        "",
        "[Layout — landscape sheet]",
        "- Left two thirds: three full-body views side by side at the same size and scale: front, side, back. "
        "Relaxed standing pose, arms down. All three show the same height and proportions.",
        "- Right third: four shoulder-up portraits in a 2×2 grid: " + ", ".join(expressions) + ".",
        "- Plain light grey-white background with no pattern; generous space between the figures.",
        "",
        "[Style]",
        *_style_lines(project),
        "Clean, tidy character reference look.",
        "No text, name labels, arrows, readable logos or watermarks.",
    ]
    return "\n".join(lines).rstrip() + "\n", refs


# --------------------------------------------------------------------------- page


def page_prompt(project: Project, episode, page_id) -> tuple[str, list[Path]]:
    page_id = str(page_id)
    panels = episode.panels(page_id)
    if not panels:
        raise ComicError(f"episode {episode.id} page {page_id}: no panels in script.yaml")
    where = f"{episode.id} {page_id}"
    chars = _characters(project, episode.page_characters(page_id))
    sheet_refs = _sheet_refs(project, chars, where)
    style_refs = _style_refs(project)
    refs = [p for p, _ in sheet_refs] + style_refs
    roles = [r for _, r in sheet_refs] + [STYLE_REF_ROLE + "." for _ in style_refs]

    cw, ch_ = project.canvas()
    lp = project.layout_params()
    weights = episode.page_slot_weights(page_id)
    total = sum(weights)
    n = len(panels)
    margin_pct = max(1, round(lp["margin"] / cw * 100))
    gutter_pct = max(1, round(lp["gutter"] / ch_ * 100))

    lines = [f"One comic page with {n} panel{'s' if n != 1 else ''}. {ratio_line(cw, ch_)}", ""]
    if roles:
        lines += _ref_lines(roles) + [""]
    if chars:
        lines += _character_block(chars) + [""]
    lines += ["[Page layout — must follow]",
              f"- Thin, even, plain white margin around the whole page (about {margin_pct}% of the page width).",
              f"- {n} panel{'s' if n != 1 else ''} stacked top to bottom, each using the full width, "
              "each with a thin black border."]
    if n > 1:
        lines.append(f"- Plain white gutters between panels (about {gutter_pct}% of the page height). "
                     "Nothing crosses a panel border; panels never overlap.")
        pcts = [round(w / total * 100) for w in weights]
        lines.append("- Panel heights, top to bottom: "
                     + ", ".join(f"panel {i} {p}%" for i, p in enumerate(pcts, 1)) + " of the panel area.")
    else:
        lines.append("- Nothing crosses the panel border.")
    lines.append("")
    for i, panel in enumerate(panels, 1):
        pchars = _characters(project, [str(c) for c in panel.get("characters") or []])
        lines += _panel_block(project, panel, f"Panel {i}", pchars) + [""]
    lines += ["[Style]", *_style_lines(project)]
    if n > 1:
        lines.append("Keep lighting and colouring consistent across all panels.")
    lines.append(_text_rule(project))
    return "\n".join(lines).rstrip() + "\n", refs


# --------------------------------------------------------------------------- panel


def current_page_image(episode, page_id) -> Path | None:
    """The current page image (state.yaml), else the latest version on disk."""
    rel = episode.page_state(page_id).get("page")
    if rel:
        p = episode.root / rel
        if p.is_file():
            return p
    return latest_version(episode.root / "pages", str(page_id))


def panel_slot(project: Project, episode, page_id, panel_id) -> tuple[int, int, int, int]:
    ids = episode.panel_ids(page_id)
    episode.panel(page_id, panel_id)  # raises a clear error when unknown
    idx = ids.index(str(panel_id))
    cw, ch = project.canvas()
    lp = project.layout_params()
    boxes = slot_boxes(cw, ch, episode.page_slot_weights(page_id), lp["margin"], lp["gutter"])
    return boxes[idx]


def panel_prompt(project: Project, episode, page_id, panel_id) -> tuple[str, list[Path]]:
    page_id, panel_id = str(page_id), str(panel_id)
    panel = episode.panel(page_id, panel_id)
    x0, y0, x1, y1 = panel_slot(project, episode, page_id, panel_id)
    chars = _characters(project, [str(c) for c in panel.get("characters") or []])
    sheet_refs = _sheet_refs(project, chars, f"{episode.id} {page_id}-{panel_id}")

    refs: list[Path] = []
    roles: list[str] = []
    page_img = current_page_image(episode, page_id)
    if page_img is not None:
        refs.append(page_img)
        roles.append("the current page this panel belongs to — match line art, colouring and lighting; "
                     "do not copy other panels' composition.")
    refs += [p for p, _ in sheet_refs]
    roles += [r for _, r in sheet_refs]

    w, h = x1 - x0, y1 - y0
    lines = [f"Redraw a single comic panel. {ratio_line(w, h)}", ""]
    if roles:
        lines += _ref_lines(roles) + [""]
    if chars:
        lines += _character_block(chars) + [""]
    idx = episode.panel_ids(page_id).index(panel_id) + 1
    lines += _panel_block(project, panel, f"Panel {idx} of the page", chars) + [""]
    lines += [
        "[Style]",
        *_style_lines(project),
        "Draw this one panel only, filling the whole image edge to edge, with no border, frame or white margin.",
        _text_rule(project),
    ]
    return "\n".join(lines).rstrip() + "\n", refs


# --------------------------------------------------------------------------- prompt files


def prompt_paths(project: Project, kind: str, *, character=None, episode=None, page_id=None, panel_id=None):
    """(prompt .txt, refs .yaml) locations for a sheet, page or panel prompt."""
    if kind == "sheet":
        d = character.root / "drafts"
        return d / "sheet.prompt.txt", d / "sheet.prompt.refs.yaml"
    d = episode.root / "prompts"
    name = f"{page_id}.page" if kind == "page" else f"{page_id}-{panel_id}.panel"
    return d / f"{name}.txt", d / f"{name}.refs.yaml"


def write_prompt(project: Project, txt: Path, refs_file: Path, text: str, refs: list[Path]) -> None:
    txt.parent.mkdir(parents=True, exist_ok=True)
    txt.write_text(text, encoding="utf-8", newline="\n")
    save_yaml(refs_file, [project.rel(p) for p in refs])


def read_refs(project: Project, refs_file: Path) -> list[Path] | None:
    """Ref paths from a .refs.yaml (None when the file does not exist)."""
    if not refs_file.is_file():
        return None
    data = load_yaml(refs_file, [])
    if not isinstance(data, list):
        raise ComicError(f"{refs_file} must be a YAML list of image paths")
    refs = [project.path(str(r)) for r in data if r]
    for r in refs:
        if not r.is_file():
            raise ComicError(f"{refs_file}: reference image not found: {r}")
    return refs


def assemble(project: Project, kind: str, *, character=None, episode=None, page_id=None, panel_id=None):
    if kind == "sheet":
        return sheet_prompt(project, character)
    if kind == "page":
        return page_prompt(project, episode, page_id)
    if kind == "panel":
        return panel_prompt(project, episode, page_id, panel_id)
    raise ValueError(kind)
