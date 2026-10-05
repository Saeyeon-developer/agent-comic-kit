"""codex-relay backend: runs imagen.ps1 (https://github.com/Saeyeon-developer/codex-image-relay).

comic.yaml:
  backend:
    name: codex-relay
    relay: C:/path/to/imagen.ps1   # or env COMIC_RELAY
    prompt_mode: Final             # Final | Draft
    timeout: 900                   # optional, seconds
    relay_command: null            # optional: command list that replaces
                                   # "powershell -NoProfile -ExecutionPolicy Bypass -File <relay>"
                                   # (used by tests to run a fake relay)

The aspect ratio is written in the prompt; -Orientation is never passed.

Relay output: success ends with `OK <path> (...)`; failure prints one `FAIL <reason>` line on stdout.
Windows PowerShell 5.1 writes stderr (and sometimes stdout) in the console code page, not UTF-8,
so output is decoded line by line: UTF-8 first, then the console code pages.
"""

from __future__ import annotations

import codecs
import locale
import os
import re
import subprocess
import sys
from pathlib import Path

from comic.backends import Backend
from comic.project import ComicError, Project

RELAY_URL = "https://github.com/Saeyeon-developer/codex-image-relay"
DEFAULT_TIMEOUT = 900
OK_RE = re.compile(r"^OK\s+(.+?)\s+\((.*)\)\s*$")
FAIL_RE = re.compile(r"^FAIL\s+(.+?)\s*$")
# PowerShell Write-Error decoration: "+ CategoryInfo ...", "+ FullyQualifiedErrorId ...", "+ ~~~~",
# the echoed source line "+ ... code ...", and the position line in any language
# ("At line:1 char:1", "At C:\x\imagen.ps1:76 char:52", Korean "<wichi> C:\x\imagen.ps1:76 <munja>:52").
PS_NOISE_RE = re.compile(r"^\s*\+(\s|~|$)|^\s*\S+\s+(line:\d+|\S.*:\d+)\s+\S+:\d+\s*$")
TAIL_LINES = 5


def console_encodings() -> list[str]:
    """Encodings to try after UTF-8: the OEM console code page (Windows), then the locale's."""
    names: list[str] = []
    if sys.platform == "win32":
        try:
            import ctypes

            names.append(f"cp{ctypes.windll.kernel32.GetOEMCP()}")
        except Exception:  # noqa: BLE001 - best effort only
            pass
    names.append(locale.getpreferredencoding(False))
    out: list[str] = []
    for name in names:
        try:
            canon = codecs.lookup(name).name
        except LookupError:
            continue
        if canon != "utf-8" and canon not in out:
            out.append(canon)
    return out


def decode_output(data: bytes | None) -> str:
    """Decode relay output line by line (stdout and stderr lines may use different encodings)."""
    if not data:
        return ""
    encodings = console_encodings()
    lines = []
    for raw in data.splitlines():
        for enc in ("utf-8", *encodings):
            try:
                lines.append(raw.decode(enc))
                break
            except UnicodeDecodeError:
                continue
        else:
            lines.append(raw.decode("utf-8", errors="replace"))
    if lines and lines[0].startswith("\ufeff"):
        lines[0] = lines[0][1:]
    return "\n".join(lines)


def failure_reason(lines: list[str]) -> str:
    """The relay's `FAIL <reason>` line, else the last meaningful lines without PowerShell decoration."""
    for ln in reversed(lines):
        m = FAIL_RE.match(ln.strip())
        if m:
            return m.group(1)
    meaningful = [ln.strip() for ln in lines if ln.strip() and not PS_NOISE_RE.match(ln)]
    return " | ".join(meaningful[-TAIL_LINES:]) or "(no output)"


def find_relay(project: Project) -> Path:
    cfg = project.section("backend")
    raw = cfg.get("relay") or os.environ.get("COMIC_RELAY")
    if not raw:
        raise ComicError(
            "no image relay configured: set backend.relay in comic.yaml to the path of imagen.ps1 "
            f"or set the environment variable COMIC_RELAY (get it from {RELAY_URL}); "
            "or set backend.name: agent to generate images yourself"
        )
    path = project.path(str(raw))
    if not path.is_file():
        raise ComicError(f"image relay not found: {path} (backend.relay / COMIC_RELAY; see {RELAY_URL})")
    return path


class CodexRelayBackend(Backend):
    name = "codex-relay"

    def __init__(self, project: Project):
        self.project = project
        cfg = project.section("backend")
        self.prompt_mode = str(cfg.get("prompt_mode") or "Final")
        try:
            self.timeout = int(cfg.get("timeout") or DEFAULT_TIMEOUT)
        except (TypeError, ValueError):
            raise ComicError("comic.yaml backend.timeout must be a number of seconds") from None
        self.relay_command = cfg.get("relay_command")
        self.info: str | None = None  # text in brackets of the relay OK line

    def command(self, prompt_file: Path, refs: list[Path], output: Path) -> list[str]:
        if self.relay_command:
            if not isinstance(self.relay_command, list) or not all(isinstance(x, str) for x in self.relay_command):
                raise ComicError("comic.yaml backend.relay_command must be a list of strings")
            cmd = list(self.relay_command)
        else:
            relay = find_relay(self.project)
            cmd = ["powershell", "-NoProfile", "-ExecutionPolicy", "Bypass", "-File", str(relay)]
        cmd += ["-PromptFile", str(Path(prompt_file).resolve()), "-Output", str(Path(output).resolve()),
                "-PromptMode", self.prompt_mode]
        if refs:
            for r in refs:
                if "," in str(Path(r).resolve()):
                    raise ComicError(f"reference path contains a comma, which the relay cannot accept: {r}")
            cmd += ["-Reference", ",".join(str(Path(r).resolve()) for r in refs)]
        return cmd

    def generate(self, prompt_file: Path, refs: list[Path], output: Path, *, import_command: str | None = None) -> Path:
        output = Path(output).resolve()
        output.parent.mkdir(parents=True, exist_ok=True)
        cmd = self.command(prompt_file, refs, output)
        try:
            proc = subprocess.run(
                cmd, stdout=subprocess.PIPE, stderr=subprocess.STDOUT, stdin=subprocess.DEVNULL,
                timeout=self.timeout,
            )
        except FileNotFoundError as e:
            raise ComicError(f"cannot run the image relay ({cmd[0]}): {e}") from None
        except subprocess.TimeoutExpired:
            raise ComicError(f"image relay timed out after {self.timeout}s; nothing was written to {output}") from None
        lines = [ln.rstrip() for ln in decode_output(proc.stdout).splitlines() if ln.strip()]
        ok = next((m for m in (OK_RE.match(ln.strip()) for ln in reversed(lines)) if m), None)
        if proc.returncode != 0 or ok is None or not output.is_file():
            tail = failure_reason(lines)
            why = f"exit code {proc.returncode}" if proc.returncode != 0 else "no image written"
            raise ComicError(f"image relay failed ({why}): {tail}")
        self.info = ok.group(2)
        return output
