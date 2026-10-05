"""agent backend: the calling AI agent generates the image with its own tool.

`generate` writes `<output>.request.yaml` and raises AgentActionRequired; the
CLI prints the request and exits 3. The agent makes the image, saves it, and
runs the `comic import ...` command from the message.
"""

from __future__ import annotations

from pathlib import Path

from comic.backends import Backend
from comic.project import ComicWaiting, Project, save_yaml


class AgentActionRequired(ComicWaiting):
    """Raised when the agent has to generate an image itself (exit code 3)."""

    def __init__(self, message: str, request_file: Path):
        super().__init__(message)
        self.request_file = request_file


def request_path(output: Path) -> Path:
    output = Path(output)
    return output.with_name(output.name + ".request.yaml")


class AgentBackend(Backend):
    name = "agent"

    def __init__(self, project: Project):
        self.project = project

    def generate(self, prompt_file: Path, refs: list[Path], output: Path, *, import_command: str | None = None) -> Path:
        prompt_file = Path(prompt_file).resolve()
        output = Path(output).resolve()
        refs = [Path(r).resolve() for r in refs]
        output.parent.mkdir(parents=True, exist_ok=True)
        req = request_path(output)
        save_yaml(req, {
            "prompt_file": str(prompt_file),
            "refs": [str(r) for r in refs],
            "output": str(output),
            "import": import_command,
        })
        lines = [
            "WAITING: generate this image with your own image tool, then import it.",
            f"prompt: {prompt_file}",
        ]
        if refs:
            lines.append("references (attach in this order; the prompt calls them Image 1, Image 2, ...):")
            lines += [f"  {i}. {r}" for i, r in enumerate(refs, 1)]
        else:
            lines.append("references: none")
        lines.append(f"save the image as PNG to: {output}")
        if import_command:
            lines.append(f"then run: {import_command}")
        lines.append(f"request written: {req}")
        raise AgentActionRequired("\n".join(lines), req)
