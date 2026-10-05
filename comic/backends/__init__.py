"""Image generation backends.

A backend turns (prompt file, reference images, output path) into an image at
the output path. `codex-relay` runs the codex-image-relay script; `agent` hands
the request to the calling AI agent and waits for `comic import`.
"""

from __future__ import annotations

from pathlib import Path

from comic.project import ComicError, Project


class Backend:
    name = "base"

    def generate(self, prompt_file: Path, refs: list[Path], output: Path, *, import_command: str | None = None) -> Path:
        """Write the image to `output` and return it.

        `import_command` is the `comic import ...` line the agent backend tells
        the agent to run after it saved the image; other backends ignore it.
        """
        raise NotImplementedError


def get_backend(project: Project) -> Backend:
    cfg = project.section("backend")
    name = str(cfg.get("name") or "codex-relay")
    if name == "codex-relay":
        from comic.backends.codex_relay import CodexRelayBackend

        return CodexRelayBackend(project)
    if name == "agent":
        from comic.backends.agent import AgentBackend

        return AgentBackend(project)
    raise ComicError(f"comic.yaml backend.name '{name}' is unknown (use codex-relay or agent)")
