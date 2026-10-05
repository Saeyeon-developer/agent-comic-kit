"""Output format presets: presets/formats/*.yaml, deep-merged with overrides."""

from __future__ import annotations

import copy
from pathlib import Path

import yaml

from comic import PRESETS_DIR
from comic.project import ComicError

LAYOUT_DEFAULTS = {"margin": 36, "gutter": 24, "border": 4}


def presets_dir() -> Path:
    """Directory holding the format presets (module attribute so tests can patch it)."""
    return PRESETS_DIR


def deep_merge(base: dict, overrides: dict | None) -> dict:
    """Return a new dict: overrides merged into base recursively (dicts merge, other values replace)."""
    result = copy.deepcopy(base)
    for key, value in (overrides or {}).items():
        if isinstance(value, dict) and isinstance(result.get(key), dict):
            result[key] = deep_merge(result[key], value)
        else:
            result[key] = copy.deepcopy(value)
    return result


def _read(path: Path) -> dict:
    try:
        data = yaml.safe_load(path.read_text(encoding="utf-8"))
    except yaml.YAMLError as e:
        raise ComicError(f"invalid YAML in preset {path}: {e}".replace("\n", " ")) from e
    if not isinstance(data, dict):
        raise ComicError(f"preset {path} is not a mapping")
    data.setdefault("name", path.stem)
    return data


def list_presets(directory: Path | None = None) -> list[dict]:
    """All presets in the presets directory, sorted by name."""
    d = Path(directory) if directory else presets_dir()
    if not d.is_dir():
        return []
    return sorted((_read(p) for p in d.glob("*.yaml")), key=lambda p: str(p.get("name")))


def preset_names(directory: Path | None = None) -> list[str]:
    d = Path(directory) if directory else presets_dir()
    return sorted(p.stem for p in d.glob("*.yaml")) if d.is_dir() else []


def load_preset(name: str, overrides: dict | None = None, directory: Path | None = None) -> dict:
    """Load preset NAME (file stem in the presets dir) merged with overrides.

    The result always has width, height (ints) and layout.{margin,gutter,border}.
    """
    if not name:
        raise ComicError("no format preset set (comic.yaml format.preset); run `comic presets` to list them")
    d = Path(directory) if directory else presets_dir()
    path = d / f"{name}.yaml"
    if not path.is_file():
        known = ", ".join(preset_names(d)) or "none found in " + str(d)
        raise ComicError(f"unknown preset '{name}' (available: {known})")
    data = deep_merge(_read(path), overrides)
    data["layout"] = deep_merge(LAYOUT_DEFAULTS, data.get("layout") or {})
    for key in ("width", "height"):
        try:
            data[key] = int(data[key])
        except (KeyError, TypeError, ValueError):
            raise ComicError(f"preset '{name}' needs an integer '{key}'") from None
        if data[key] <= 0:
            raise ComicError(f"preset '{name}': '{key}' must be positive")
    return data
