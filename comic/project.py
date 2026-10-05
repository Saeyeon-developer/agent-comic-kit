"""Project data model: comic.yaml, cast, episodes, state, versioning."""

from __future__ import annotations

import re
from pathlib import Path

import yaml

DIALOGUE_TYPES = ("speech", "shout", "thought", "narration")
READING_DIRECTIONS = ("ltr", "rtl", "ttb")
LETTERING_MODES = ("overlay", "in-image")
BACKENDS = ("codex-relay", "agent")
CHARACTER_STATUSES = ("draft", "approved")


class ComicError(Exception):
    """User-facing error: the CLI prints it as one line on stderr and exits 1."""


class ComicWaiting(Exception):
    """Work is waiting for the agent: the CLI prints the message on stdout and exits 3."""

    exit_code = 3


# --------------------------------------------------------------------------- yaml helpers


def load_yaml(path: Path, default=None):
    """Read a YAML file (UTF-8). Missing file -> default. Bad YAML -> ComicError."""
    path = Path(path)
    if not path.is_file():
        return default
    try:
        data = yaml.safe_load(path.read_text(encoding="utf-8"))
    except yaml.YAMLError as e:
        raise ComicError(f"invalid YAML in {path}: {' '.join(str(e).split())}") from e
    return default if data is None else data


def save_yaml(path: Path, data) -> Path:
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    text = yaml.safe_dump(data, allow_unicode=True, sort_keys=False, default_flow_style=None, width=100)
    path.write_text(text, encoding="utf-8")
    return path


# --------------------------------------------------------------------------- versioning


def _version_re(stem: str, ext: str) -> re.Pattern:
    return re.compile(rf"^{re.escape(stem)}\.v(\d+){re.escape(ext)}$", re.IGNORECASE)


def versions(directory: Path, stem: str, ext: str = ".png") -> list[tuple[int, Path]]:
    """Existing (N, path) for stem.vN.ext in directory, sorted by N."""
    directory = Path(directory)
    if not directory.is_dir():
        return []
    pat = _version_re(stem, ext)
    found = []
    for p in directory.iterdir():
        m = pat.match(p.name)
        if m and p.is_file():
            found.append((int(m.group(1)), p))
    return sorted(found)


def latest_version(directory: Path, stem: str, ext: str = ".png") -> Path | None:
    v = versions(directory, stem, ext)
    return v[-1][1] if v else None


def next_version(directory: Path, stem: str, ext: str = ".png") -> Path:
    """directory/stem.v{N}{ext} with N = highest existing + 1 (1 if none). Creates directory."""
    directory = Path(directory)
    directory.mkdir(parents=True, exist_ok=True)
    v = versions(directory, stem, ext)
    n = v[-1][0] + 1 if v else 1
    return directory / f"{stem}.v{n}{ext}"


# --------------------------------------------------------------------------- model


class Project:
    def __init__(self, root: Path, config: dict):
        self.root = Path(root)
        self.config = config

    @classmethod
    def load(cls, root) -> "Project":
        root = Path(root).expanduser().resolve()
        cfg_path = root / "comic.yaml"
        if not root.is_dir():
            raise ComicError(f"project folder not found: {root}")
        if not cfg_path.is_file():
            raise ComicError(f"no comic.yaml in {root}; create a project with: comic init {root}")
        config = load_yaml(cfg_path, {})
        if not isinstance(config, dict):
            raise ComicError(f"{cfg_path} must be a YAML mapping")
        return cls(root, config)

    # paths ---------------------------------------------------------------
    def path(self, rel) -> Path:
        """Resolve a project-relative path (absolute paths pass through)."""
        p = Path(rel).expanduser()
        return p if p.is_absolute() else self.root / p

    def rel(self, path) -> str:
        """Project-relative POSIX-style string for a path (absolute if outside the project)."""
        p = Path(path)
        try:
            return p.resolve().relative_to(self.root.resolve()).as_posix()
        except ValueError:
            return str(p)

    def save_config(self) -> Path:
        return save_yaml(self.root / "comic.yaml", self.config)

    # config sections -----------------------------------------------------
    def section(self, name: str) -> dict:
        value = self.config.get(name)
        return value if isinstance(value, dict) else {}

    @property
    def title(self) -> str:
        return str(self.config.get("title") or self.root.name)

    @property
    def language(self) -> str:
        return str(self.config.get("language") or "en")

    @property
    def reading_direction(self) -> str:
        return str(self.config.get("reading_direction") or "ltr")

    # preset / geometry ---------------------------------------------------
    def preset(self) -> dict:
        from comic.presets import load_preset

        fmt = self.section("format")
        overrides = fmt.get("overrides") or {}
        if not isinstance(overrides, dict):
            raise ComicError("comic.yaml format.overrides must be a mapping")
        return load_preset(fmt.get("preset"), overrides)

    def canvas(self) -> tuple[int, int]:
        p = self.preset()
        return p["width"], p["height"]

    def layout_params(self) -> dict:
        lay = self.preset().get("layout") or {}
        return {k: int(lay.get(k, d)) for k, d in (("margin", 36), ("gutter", 24), ("border", 4))}

    # cast ----------------------------------------------------------------
    def characters(self) -> dict[str, "Character"]:
        cast = self.root / "cast"
        result: dict[str, Character] = {}
        if cast.is_dir():
            for d in sorted(cast.iterdir()):
                if (d / "character.yaml").is_file():
                    result[d.name] = Character.load(d)
        return result

    def character(self, cid) -> "Character":
        d = self.root / "cast" / str(cid)
        if not (d / "character.yaml").is_file():
            known = ", ".join(self.characters()) or "none"
            raise ComicError(f"unknown character '{cid}' (cast: {known}); create it with: comic cast new {self.root} {cid} --name NAME")
        return Character.load(d)

    # episodes ------------------------------------------------------------
    def episode_ids(self) -> list[str]:
        eps = self.root / "episodes"
        if not eps.is_dir():
            return []
        return sorted(d.name for d in eps.iterdir() if (d / "script.yaml").is_file())

    def episode(self, ep) -> "Episode":
        root = self.root / "episodes" / str(ep)
        script_path = root / "script.yaml"
        if not script_path.is_file():
            known = ", ".join(self.episode_ids()) or "none"
            raise ComicError(f"no episode '{ep}' ({script_path} missing; episodes: {known})")
        script = load_yaml(script_path, {})
        if not isinstance(script, dict):
            raise ComicError(f"{script_path} must be a YAML mapping")
        return Episode(self, str(ep), root, script)

    # validation ----------------------------------------------------------
    def validate(self) -> list[str]:
        problems: list[str] = []
        cfg = self.config
        if not cfg.get("title"):
            problems.append("comic.yaml: title is empty")
        if self.reading_direction not in READING_DIRECTIONS:
            problems.append(f"comic.yaml: reading_direction '{self.reading_direction}' must be one of {', '.join(READING_DIRECTIONS)}")
        try:
            self.preset()
        except ComicError as e:
            problems.append(f"comic.yaml format: {e}")
        mode = self.section("lettering").get("mode", "overlay")
        if mode not in LETTERING_MODES:
            problems.append(f"comic.yaml: lettering.mode '{mode}' must be one of {', '.join(LETTERING_MODES)}")
        backend = self.section("backend").get("name", "codex-relay")
        if backend not in BACKENDS:
            problems.append(f"comic.yaml: backend.name '{backend}' must be one of {', '.join(BACKENDS)}")
        for ref in self.section("style").get("refs") or []:
            if not self.path(ref).is_file():
                problems.append(f"comic.yaml: style ref not found: {ref}")
        for cid, ch in self.characters().items():
            problems += ch.validate()
        return problems


class Character:
    def __init__(self, root: Path, data: dict):
        self.root = Path(root)
        self.id = root.name
        self.data = data

    @classmethod
    def load(cls, root: Path) -> "Character":
        path = Path(root) / "character.yaml"
        data = load_yaml(path, {})
        if not isinstance(data, dict):
            raise ComicError(f"{path} must be a YAML mapping")
        return cls(Path(root), data)

    @property
    def name(self) -> str:
        return str(self.data.get("name") or self.id)

    @property
    def status(self) -> str:
        return str(self.data.get("status") or "draft")

    def sheet_path(self) -> Path | None:
        sheet = self.data.get("sheet")
        if not sheet:
            return None
        p = Path(sheet)
        return p if p.is_absolute() else self.root / p

    @property
    def approved(self) -> bool:
        sheet = self.sheet_path()
        return self.status == "approved" and sheet is not None and sheet.is_file()

    def save(self) -> Path:
        return save_yaml(self.root / "character.yaml", self.data)

    def validate(self) -> list[str]:
        problems = []
        where = f"cast/{self.id}/character.yaml"
        if self.data.get("id") not in (None, self.id):
            problems.append(f"{where}: id '{self.data.get('id')}' does not match folder name '{self.id}'")
        if self.status not in CHARACTER_STATUSES:
            problems.append(f"{where}: status '{self.status}' must be draft or approved")
        if self.status == "approved" and not self.approved:
            problems.append(f"{where}: status is approved but sheet file is missing ({self.data.get('sheet')})")
        for inp in self.data.get("inputs") or []:
            if not (self.root / inp).is_file():
                problems.append(f"{where}: input image not found: {inp}")
        return problems


class Episode:
    def __init__(self, project: Project, ep_id: str, root: Path, script: dict):
        self.project = project
        self.id = ep_id
        self.root = Path(root)
        self.script = script

    # script --------------------------------------------------------------
    def pages(self) -> list[dict]:
        pages = self.script.get("pages") or []
        return [p for p in pages if isinstance(p, dict)] if isinstance(pages, list) else []

    def page_ids(self) -> list[str]:
        return [str(p.get("id")) for p in self.pages()]

    def page(self, page_id) -> dict:
        for p in self.pages():
            if str(p.get("id")) == str(page_id):
                return p
        raise ComicError(f"episode {self.id}: no page '{page_id}' (pages: {', '.join(self.page_ids()) or 'none'})")

    def panels(self, page_id) -> list[dict]:
        panels = self.page(page_id).get("panels") or []
        return [p for p in panels if isinstance(p, dict)] if isinstance(panels, list) else []

    def panel_ids(self, page_id) -> list[str]:
        return [str(p.get("id")) for p in self.panels(page_id)]

    def panel(self, page_id, panel_id) -> dict:
        for p in self.panels(page_id):
            if str(p.get("id")) == str(panel_id):
                return p
        raise ComicError(
            f"episode {self.id} page {page_id}: no panel '{panel_id}' (panels: {', '.join(self.panel_ids(page_id)) or 'none'})"
        )

    def page_slot_weights(self, page_id) -> list[float]:
        weights = []
        for p in self.panels(page_id):
            try:
                w = float(p.get("weight", 1) if p.get("weight") is not None else 1)
            except (TypeError, ValueError):
                raise ComicError(f"episode {self.id} {page_id}-{p.get('id')}: weight must be a number") from None
            if w <= 0:
                raise ComicError(f"episode {self.id} {page_id}-{p.get('id')}: weight must be positive")
            weights.append(w)
        return weights

    def page_characters(self, page_id) -> list[str]:
        """Character ids on a page, in order of first appearance."""
        seen: list[str] = []
        for p in self.panels(page_id):
            for c in p.get("characters") or []:
                if str(c) not in seen:
                    seen.append(str(c))
        return seen

    # tool / agent files --------------------------------------------------
    def state(self) -> dict:
        state = load_yaml(self.root / "state.yaml", {})
        if not isinstance(state, dict):
            state = {}
        if not isinstance(state.get("pages"), dict):
            state["pages"] = {}
        return state

    def save_state(self, state: dict) -> Path:
        return save_yaml(self.root / "state.yaml", state)

    def page_state(self, page_id) -> dict:
        """state['pages'][page_id] with a 'panels' dict (string keys); empty if unknown."""
        ps = self.state()["pages"].get(str(page_id)) or {}
        panels = ps.get("panels") or {}
        ps["panels"] = {str(k): v for k, v in panels.items()} if isinstance(panels, dict) else {}
        return ps

    def lettering(self) -> dict:
        data = load_yaml(self.root / "lettering.yaml", {})
        return data if isinstance(data, dict) else {}

    def save_lettering(self, data: dict) -> Path:
        return save_yaml(self.root / "lettering.yaml", data)

    # validation ----------------------------------------------------------
    def validate(self) -> list[str]:
        problems: list[str] = []
        script = self.script
        if not isinstance(script.get("pages"), list) or not script.get("pages"):
            return [f"{self.id}/script.yaml: 'pages' must be a non-empty list"]
        cast = self.project.characters()
        seen_pages: set[str] = set()
        reported: set[str] = set()
        for pi, page in enumerate(script["pages"]):
            if not isinstance(page, dict) or page.get("id") in (None, ""):
                problems.append(f"{self.id}: page #{pi + 1} has no id")
                continue
            pid = str(page["id"])
            if pid in seen_pages:
                problems.append(f"{self.id}: duplicate page id '{pid}'")
            seen_pages.add(pid)
            panels = page.get("panels")
            if not isinstance(panels, list) or not panels:
                problems.append(f"{self.id} {pid}: 'panels' must be a non-empty list")
                continue
            seen_panels: set[str] = set()
            for ni, panel in enumerate(panels):
                if not isinstance(panel, dict) or panel.get("id") in (None, ""):
                    problems.append(f"{self.id} {pid}: panel #{ni + 1} has no id")
                    continue
                key = f"{pid}-{panel['id']}"
                if str(panel["id"]) in seen_panels:
                    problems.append(f"{self.id} {pid}: duplicate panel id '{panel['id']}'")
                seen_panels.add(str(panel["id"]))
                problems += self._validate_panel(key, panel, cast, reported)
        return problems

    def _validate_panel(self, key: str, panel: dict, cast: dict, reported: set) -> list[str]:
        problems = []
        weight = panel.get("weight", 1)
        if weight is not None and (not isinstance(weight, (int, float)) or isinstance(weight, bool) or weight <= 0):
            problems.append(f"{key}: weight must be a positive number (got {weight!r})")
        focus = panel.get("focus")
        if focus is not None and (not isinstance(focus, (int, float)) or not 0 <= focus <= 1):
            problems.append(f"{key}: focus must be a number from 0 to 1 (got {focus!r})")
        chars = panel.get("characters") or []
        if not isinstance(chars, list):
            problems.append(f"{key}: characters must be a list")
            chars = []
        for cid in chars:
            cid = str(cid)
            ch = cast.get(cid)
            if ch is None:
                problems.append(f"{key}: unknown character '{cid}' (no cast/{cid}/character.yaml)")
            elif not ch.approved and cid not in reported:
                reported.add(cid)
                problems.append(
                    f"{key}: character '{cid}' is not approved (make a sheet, then: comic cast approve {self.project.root} {cid})"
                )
        dialogue = panel.get("dialogue") or []
        if not isinstance(dialogue, list):
            return problems + [f"{key}: dialogue must be a list"]
        for di, line in enumerate(dialogue):
            dkey = f"{key}#{di}"
            if not isinstance(line, dict):
                problems.append(f"{dkey}: dialogue entry must be a mapping")
                continue
            dtype = line.get("type", "speech")
            if dtype not in DIALOGUE_TYPES:
                problems.append(f"{dkey}: type '{dtype}' must be one of {', '.join(DIALOGUE_TYPES)}")
            if dtype != "narration" and not line.get("speaker"):
                problems.append(f"{dkey}: speaker is required for {dtype}")
            if not str(line.get("text") or "").strip():
                problems.append(f"{dkey}: text is empty")
        return problems
