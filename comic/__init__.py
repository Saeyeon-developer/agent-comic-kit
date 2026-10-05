"""agent-comic-kit: make comics with AI image generation."""

from pathlib import Path

__version__ = "0.1.0"

# v0.1 runs from a checkout (or an editable install): presets/ and templates/
# live next to the package in the repository root.
REPO_ROOT = Path(__file__).resolve().parent.parent
PRESETS_DIR = REPO_ROOT / "presets" / "formats"
TEMPLATES_DIR = REPO_ROOT / "templates"
