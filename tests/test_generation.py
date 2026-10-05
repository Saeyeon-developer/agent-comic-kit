"""Tests for generation: prompt assembly, backends (fake relay / agent), cast/prompt/gen/import commands.

Never calls the real image relay: the codex-relay backend is pointed at a fake
Python relay through comic.yaml backend.relay_command.
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

import pytest
import yaml
from PIL import Image

import comic.presets as presets
from comic import cli, prompts
from comic.layout import aspect_text, slot_boxes
from comic.project import ComicError, Project, load_yaml, save_yaml

GENRE_WORDS = ("manga", "webtoon", "manhwa")

PRESET = {
    "name": "test-slides", "label": "Test 4:5", "kind": "slides", "width": 1080, "height": 1350,
    "slice_height": None, "max_pages": 20, "per_post": None,
    "file": {"type": "jpg", "quality": 92, "max_mb": 8},
    "layout": {"margin": 36, "gutter": 24, "border": 4}, "checked": "2026-10",
}

SCRIPT = {
    "episode": "ep01",
    "title": "Crepe Day",
    "pages": [{
        "id": "p01",
        "panels": [
            {"id": 1, "weight": 55, "shot": "medium full shot, eye level", "characters": ["bob"],
             "action": "Bob looks at a crepe stand", "background": "shopping street, daytime",
             "bubble_space": "top-left",
             "dialogue": [{"type": "narration", "text": "Saturday"},
                          {"type": "speech", "speaker": "bob", "text": "Crepes!"}]},
            {"id": 2, "weight": 45, "shot": "close-up", "characters": ["alice", "bob"],
             "action": "Alice hugs Bob", "background": "blurred street", "bubble_space": "top-right",
             "dialogue": [{"type": "shout", "speaker": "alice", "text": "Let's go!"}]},
        ],
    }],
}

FAKE_RELAY = r'''
import json, os, sys
from PIL import Image
args = sys.argv[1:]
opts = {}
i = 0
while i < len(args):
    opts[args[i]] = args[i + 1] if i + 1 < len(args) else None
    i += 2
log = os.environ.get("FAKE_RELAY_LOG")
if log:
    with open(log, "a", encoding="utf-8") as f:
        f.write(json.dumps(args) + "\n")
if os.environ.get("FAKE_RELAY_FAIL"):
    for n in range(8):
        print(f"line {n}", flush=True)
    print("codex exited with code 7.", file=sys.stderr)
    sys.exit(1)
out = opts["-Output"]
os.makedirs(os.path.dirname(out), exist_ok=True)
Image.new("RGB", (40, 50), "white").save(out)
with open(os.path.splitext(out)[0] + ".json", "w", encoding="utf-8") as f:
    json.dump({"output": out}, f)
print("OK " + out + " (40x50, 123 bytes, 1.0s, fake/low)")
'''


# --------------------------------------------------------------------------- fixtures


@pytest.fixture(autouse=True)
def preset_dir(tmp_path, monkeypatch):
    d = tmp_path / "presets"
    d.mkdir()
    save_yaml(d / "test-slides.yaml", PRESET)
    monkeypatch.setattr(presets, "PRESETS_DIR", d)
    monkeypatch.delenv("COMIC_RELAY", raising=False)
    return d


@pytest.fixture
def fake_relay(tmp_path, monkeypatch):
    script = tmp_path / "fake_relay.py"
    script.write_text(FAKE_RELAY, encoding="utf-8")
    log = tmp_path / "relay-calls.jsonl"
    monkeypatch.setenv("FAKE_RELAY_LOG", str(log))
    monkeypatch.delenv("FAKE_RELAY_FAIL", raising=False)
    return {"command": [sys.executable, str(script)], "log": log}


def make_project(root: Path, backend=None, style=None, lettering="overlay") -> Path:
    root.mkdir(parents=True, exist_ok=True)
    save_yaml(root / "comic.yaml", {
        "title": "Crepe Day", "language": "en", "reading_direction": "ltr",
        "format": {"preset": "test-slides", "overrides": {}},
        "style": style or {"description": "clean line art, cel shading", "color": "full colour", "refs": []},
        "lettering": {"mode": lettering, "font": None, "size": 30},
        "backend": backend or {"name": "agent"},
    })
    return root


def add_character(root: Path, cid: str, approved=True, height=None, appearance=None):
    d = root / "cast" / cid
    d.mkdir(parents=True, exist_ok=True)
    data = {"id": cid, "name": cid.title(), "status": "draft",
            "appearance": appearance or f"{cid} appearance: unique-{cid}-look",
            "height": height, "expressions": ["grin", "wink"], "inputs": [], "sheet": None}
    if approved:
        (d / "refs").mkdir(exist_ok=True)
        Image.new("RGB", (30, 20), "white").save(d / "refs" / "sheet.png")
        data.update(status="approved", sheet="refs/sheet.png")
    save_yaml(d / "character.yaml", data)


def setup_project(tmp_path, **kw) -> Path:
    root = make_project(tmp_path / "proj", **kw)
    add_character(root, "alice", height="tall; clearly taller than Bob")
    add_character(root, "bob", height="short")
    save_yaml(root / "episodes" / "ep01" / "script.yaml", SCRIPT)
    return root


def run(*argv) -> int:
    return cli.main([str(a) for a in argv])


def png(path: Path, size=(40, 50), fmt=None) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    Image.new("RGB", size, "gray").save(path, fmt)
    return path


def no_genre_words(text: str):
    low = text.lower()
    for w in GENRE_WORDS:
        assert w not in low, f"genre word '{w}' in prompt"


# --------------------------------------------------------------------------- prompt assembly


def test_page_prompt(tmp_path):
    root = setup_project(tmp_path)
    project = Project.load(root)
    ep = project.episode("ep01")
    text, refs = prompts.page_prompt(project, ep, "p01")
    first = text.splitlines()[0]
    assert first.startswith("One comic page with 2 panels.")
    assert "The whole image is exactly 4:5 (slightly taller than wide)." in first
    # refs: sheets in order of first appearance (bob in panel 1, then alice)
    assert refs == [root / "cast" / "bob" / "refs" / "sheet.png", root / "cast" / "alice" / "refs" / "sheet.png"]
    assert "Image 1: character sheet of Bob" in text
    assert "Image 2: character sheet of Alice" in text
    assert "do not copy the sheet's background, layout or expression grid" in text
    for cid in ("alice", "bob"):
        assert f"unique-{cid}-look" in text
    assert "Alice — tall; clearly taller than Bob." in text and "Bob — short." in text
    assert "panel 1 55%, panel 2 45%" in text
    assert "Keep the top-left area simple and empty for speech bubbles." in text
    assert "Keep the top-right area simple" in text
    assert "medium full shot, eye level" in text
    assert "Clean line art, cel shading." in text and "Colour: full colour." in text
    assert "No speech bubbles, text, sound effects, readable signs or watermarks." in text
    assert "Crepes!" not in text  # overlay mode: dialogue is lettered later
    no_genre_words(text)


def test_page_prompt_in_image_and_style_refs(tmp_path):
    root = setup_project(tmp_path, lettering="in-image")
    png(root / "style" / "look.png")
    cfg = load_yaml(root / "comic.yaml")
    cfg["style"]["refs"] = ["style/look.png"]
    save_yaml(root / "comic.yaml", cfg)
    project = Project.load(root)
    text, refs = prompts.page_prompt(project, project.episode("ep01"), "p01")
    assert refs[-1] == root / "style" / "look.png" and len(refs) == 3
    assert "Image 3: style reference" in text
    assert 'speech bubble (round, with a tail to Bob): "Crepes!"' in text
    assert 'narration box (rectangular caption box): "Saturday"' in text
    assert "shout bubble" in text and "Let's go!" in text
    assert "No speech bubbles" not in text
    no_genre_words(text)


def test_genre_words_only_from_user_style(tmp_path):
    root = setup_project(tmp_path, style={"description": "black and white manga style", "color": "monochrome"})
    project = Project.load(root)
    text, _ = prompts.page_prompt(project, project.episode("ep01"), "p01")
    assert text.lower().count("manga") == 1  # only the user's own words


def test_panel_prompt_aspect_and_refs(tmp_path):
    root = setup_project(tmp_path)
    project = Project.load(root)
    ep = project.episode("ep01")
    page = png(ep.root / "pages" / "p01.v1.png")
    ep.save_state({"pages": {"p01": {"page": "pages/p01.v1.png", "panels": {}}}})
    text, refs = prompts.panel_prompt(project, ep, "p01", "2")
    x0, y0, x1, y1 = slot_boxes(1080, 1350, [55, 45], 36, 24)[1]
    expected = aspect_text(x1 - x0, y1 - y0)
    assert text.splitlines()[0].startswith(f"Redraw a single comic panel. The whole image is exactly {expected} (")
    assert refs[0] == page
    assert refs[1:] == [root / "cast" / "alice" / "refs" / "sheet.png", root / "cast" / "bob" / "refs" / "sheet.png"]
    assert "Image 1: the current page this panel belongs to" in text
    assert "no border" in text
    assert "unique-alice-look" in text and "unique-bob-look" in text
    no_genre_words(text)


def test_sheet_prompt(tmp_path):
    root = setup_project(tmp_path)
    png(root / "cast" / "alice" / "inputs" / "front.png")
    ch_yaml = root / "cast" / "alice" / "character.yaml"
    data = load_yaml(ch_yaml)
    data["inputs"] = ["inputs/front.png"]
    save_yaml(ch_yaml, data)
    project = Project.load(root)
    text, refs = prompts.sheet_prompt(project, project.character("alice"))
    assert "The whole image is exactly 3:2 (wider than tall)." in text.splitlines()[0]
    assert refs == [root / "cast" / "alice" / "inputs" / "front.png"]
    assert "Source image 1: Alice — carry over the design only" in text
    assert "grin, wink, neutral, smile" in text  # padded to four expressions
    assert "unique-alice-look" in text
    no_genre_words(text)


def test_prompt_command_writes_files(tmp_path, capsys):
    root = setup_project(tmp_path)
    assert run("prompt", "page", root, "ep01", "p01") == 0
    txt = root / "episodes" / "ep01" / "prompts" / "p01.page.txt"
    refs = load_yaml(root / "episodes" / "ep01" / "prompts" / "p01.page.refs.yaml")
    assert txt.is_file() and refs == ["cast/bob/refs/sheet.png", "cast/alice/refs/sheet.png"]
    out = capsys.readouterr().out
    assert str(txt) in out
    assert run("prompt", "panel", root, "ep01", "p01", "1") == 0
    assert (root / "episodes" / "ep01" / "prompts" / "p01-1.panel.txt").is_file()
    assert run("prompt", "sheet", root, "bob") == 0
    assert (root / "cast" / "bob" / "drafts" / "sheet.prompt.txt").is_file()
    assert (root / "cast" / "bob" / "drafts" / "sheet.prompt.refs.yaml").is_file()


# --------------------------------------------------------------------------- approval gate


def test_gate_blocks_gen_page(tmp_path, capsys, fake_relay):
    root = setup_project(tmp_path, backend={"name": "codex-relay", "relay_command": fake_relay["command"]})
    add_character(root, "bob", approved=False)
    assert run("gen", "page", root, "ep01", "p01") == 1
    err = capsys.readouterr().err
    assert "character 'bob' is not approved" in err
    assert "fix them before generating" in err
    assert not (root / "episodes" / "ep01" / "pages").exists()
    assert not fake_relay["log"].exists()


# --------------------------------------------------------------------------- codex-relay backend (fake)


def test_codex_relay_gen_page(tmp_path, capsys, fake_relay):
    root = setup_project(tmp_path, backend={"name": "codex-relay", "relay_command": fake_relay["command"],
                                            "prompt_mode": "Final"})
    ep_root = root / "episodes" / "ep01"
    save_yaml(ep_root / "state.yaml", {"pages": {"p01": {"page": None, "panels": {"1": "panels/p01-1.v1.png"}}}})
    assert run("gen", "page", root, "ep01", "p01") == 0
    out = capsys.readouterr().out
    img = ep_root / "pages" / "p01.v1.png"
    assert img.is_file() and str(img) in out
    state = load_yaml(ep_root / "state.yaml")
    assert state["pages"]["p01"] == {"page": "pages/p01.v1.png", "panels": {}}
    args = json.loads(fake_relay["log"].read_text(encoding="utf-8").splitlines()[0])
    assert "-Orientation" not in args
    assert args[args.index("-PromptMode") + 1] == "Final"
    assert args[args.index("-Output") + 1] == str(img.resolve())
    ref_arg = args[args.index("-Reference") + 1]
    assert ref_arg.split(",") == [str((root / "cast" / c / "refs" / "sheet.png").resolve()) for c in ("bob", "alice")]
    assert args.count("-Reference") == 1

    # the existing (agent-edited) prompt is used as-is
    prompt_file = ep_root / "prompts" / "p01.page.txt"
    prompt_file.write_text("EDITED PROMPT\n", encoding="utf-8")
    assert run("gen", "page", root, "ep01", "p01") == 0
    assert (ep_root / "pages" / "p01.v2.png").is_file()
    assert prompt_file.read_text(encoding="utf-8") == "EDITED PROMPT\n"
    assert load_yaml(ep_root / "state.yaml")["pages"]["p01"]["page"] == "pages/p01.v2.png"
    # --fresh re-assembles
    assert run("gen", "page", root, "ep01", "p01", "--fresh") == 0
    assert prompt_file.read_text(encoding="utf-8").startswith("One comic page")
    assert (ep_root / "pages" / "p01.v3.png").is_file()


def test_codex_relay_gen_panel_keeps_state(tmp_path, capsys, fake_relay):
    root = setup_project(tmp_path, backend={"name": "codex-relay", "relay_command": fake_relay["command"]})
    ep_root = root / "episodes" / "ep01"
    png(ep_root / "pages" / "p01.v1.png")
    state = {"pages": {"p01": {"page": "pages/p01.v1.png", "panels": {"2": "panels/p01-2.v1.png"}}}}
    save_yaml(ep_root / "state.yaml", state)
    png(ep_root / "panels" / "p01-2.v1.png")
    assert run("gen", "panel", root, "ep01", "p01", "2") == 0
    assert (ep_root / "panels" / "p01-2.v2.png").is_file()
    assert load_yaml(ep_root / "state.yaml") == state
    assert "comic use" in capsys.readouterr().out
    args = json.loads(fake_relay["log"].read_text(encoding="utf-8").splitlines()[0])
    refs = args[args.index("-Reference") + 1].split(",")
    assert refs[0] == str((ep_root / "pages" / "p01.v1.png").resolve())


def test_codex_relay_failure(tmp_path, capsys, fake_relay, monkeypatch):
    root = setup_project(tmp_path, backend={"name": "codex-relay", "relay_command": fake_relay["command"]})
    monkeypatch.setenv("FAKE_RELAY_FAIL", "1")
    assert run("gen", "sheet", root, "alice") == 1
    err = capsys.readouterr().err
    assert "image relay failed (exit code 1)" in err
    assert "codex exited with code 7." in err and "line 7" in err
    assert "line 2" not in err  # only the last 5 lines


FAKE_RELAY_PS_ERROR = r'''
import os, sys
# What imagen.ps1 prints under Windows PowerShell 5.1 on a Korean-locale console: an optional
# FAIL line on stdout, then Write-Error decoration on stderr in the OEM code page (cp949).
if os.environ.get("FAKE_RELAY_FAIL_LINE"):
    print("retry 1/2 after 20s: Selected model is at capacity. Please try a different model.", flush=True)
    print("FAIL Selected model is at capacity. Please try a different model. (after 3 attempts)", flush=True)
noise = (
    "C:\\relay\\imagen.ps1 : codex exited with code 1.\r\n"
    + os.environ.get("FAKE_RELAY_STDERR_MSG", "") +
    "\uc704\uce58 C:\\relay\\imagen.ps1:192 \ubb38\uc790:1\r\n"
    "+ if ($proc.ExitCode -ne 0) { & $fail \"codex exited\" }\r\n"
    "+ ~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~\r\n"
    "    + CategoryInfo          : NotSpecified: (:) [Write-Error], WriteErrorException\r\n"
    "    + FullyQualifiedErrorId : Microsoft.PowerShell.Commands.WriteErrorException,imagen.ps1\r\n"
    " \r\n"
)
sys.stderr.buffer.write(noise.encode("cp949"))
sys.stderr.flush()
sys.exit(1)
'''


@pytest.fixture
def ps_error_relay(tmp_path, monkeypatch):
    from comic.backends import codex_relay

    script = tmp_path / "fake_relay_ps_error.py"
    script.write_text(FAKE_RELAY_PS_ERROR, encoding="utf-8")
    # decode as on a Korean-locale console regardless of this machine's code pages
    monkeypatch.setattr(codex_relay, "console_encodings", lambda: ["cp949"])
    monkeypatch.delenv("FAKE_RELAY_FAIL_LINE", raising=False)
    monkeypatch.delenv("FAKE_RELAY_STDERR_MSG", raising=False)
    return [sys.executable, str(script)]


def test_codex_relay_fail_line_with_cp949_noise(tmp_path, capsys, ps_error_relay, monkeypatch):
    root = setup_project(tmp_path, backend={"name": "codex-relay", "relay_command": ps_error_relay})
    monkeypatch.setenv("FAKE_RELAY_FAIL_LINE", "1")
    project = Project.load(root)
    from comic.backends import get_backend

    prompt = tmp_path / "p.txt"
    prompt.write_text("hello", encoding="utf-8")
    with pytest.raises(ComicError) as exc:
        get_backend(project).generate(prompt, [], tmp_path / "out" / "x.png")
    msg = str(exc.value)
    assert msg == ("image relay failed (exit code 1): "
                   "Selected model is at capacity. Please try a different model. (after 3 attempts)")
    # and through the CLI
    assert run("gen", "sheet", root, "alice") == 1
    err = capsys.readouterr().err
    assert "Selected model is at capacity" in err
    assert "CategoryInfo" not in err and "\ufffd" not in err


def test_codex_relay_ps_noise_without_fail_line(tmp_path, ps_error_relay, monkeypatch):
    """An older relay without the FAIL line: decode cp949 and drop the PowerShell decoration."""
    from comic.backends import get_backend

    root = setup_project(tmp_path, backend={"name": "codex-relay", "relay_command": ps_error_relay})
    monkeypatch.setenv("FAKE_RELAY_STDERR_MSG", "\uc11c\ubc84 \uacfc\ubd80\ud558: Selected model is at capacity.\r\n")
    prompt = tmp_path / "p.txt"
    prompt.write_text("hello", encoding="utf-8")
    with pytest.raises(ComicError) as exc:
        get_backend(Project.load(root)).generate(prompt, [], tmp_path / "out" / "x.png")
    msg = str(exc.value)
    assert msg == ("image relay failed (exit code 1): C:\\relay\\imagen.ps1 : codex exited with code 1. | "
                   "\uc11c\ubc84 \uacfc\ubd80\ud558: Selected model is at capacity.")


def test_decode_output_mixed_encodings(monkeypatch):
    from comic.backends import codex_relay

    monkeypatch.setattr(codex_relay, "console_encodings", lambda: ["cp949"])
    data = "OK \ud55c\uae00.png\r\n".encode("utf-8") + "\uc704\uce58 x:1 \ubb38\uc790:1\r\n".encode("cp949") + b"\xff\xfe\xff\n"
    lines = codex_relay.decode_output(data).splitlines()
    assert lines[0] == "OK \ud55c\uae00.png"
    assert lines[1] == "\uc704\uce58 x:1 \ubb38\uc790:1"
    assert len(lines) == 3 and "�" in lines[2]  # undecodable line kept with replacement characters


def test_console_encodings_real():
    from comic.backends import codex_relay

    encs = codex_relay.console_encodings()
    assert "utf-8" not in encs and len(encs) == len(set(encs))


def test_codex_relay_missing(tmp_path, capsys):
    root = setup_project(tmp_path, backend={"name": "codex-relay"})
    assert run("gen", "sheet", root, "alice") == 1
    assert "COMIC_RELAY" in capsys.readouterr().err


# --------------------------------------------------------------------------- agent backend + import


def test_agent_gen_page_and_import(tmp_path, capsys):
    root = setup_project(tmp_path)
    ep_root = root / "episodes" / "ep01"
    assert run("gen", "page", root, "ep01", "p01") == 3
    out = capsys.readouterr().out
    target = ep_root / "pages" / "p01.v1.png"
    req = ep_root / "pages" / "p01.v1.png.request.yaml"
    assert req.is_file()
    data = load_yaml(req)
    assert data["output"] == str(target.resolve())
    assert data["refs"] == [str((root / "cast" / c / "refs" / "sheet.png").resolve()) for c in ("bob", "alice")]
    assert str((ep_root / "prompts" / "p01.page.txt").resolve()) in out
    assert f"save the image as PNG to: {target.resolve()}" in out
    assert "then run: comic import" in out and " ep01 page p01 " in out
    assert "  1. " in out and "  2. " in out
    assert not target.exists()

    # the agent saved the image at the requested path, then imports it: adopted in place
    png(target)
    assert run("import", root, "ep01", "page", "p01", target) == 0
    assert target.is_file() and not (ep_root / "pages" / "p01.v2.png").exists()
    assert not req.exists()
    assert load_yaml(ep_root / "state.yaml")["pages"]["p01"]["page"] == "pages/p01.v1.png"

    # an image saved elsewhere (JPEG) is converted into the next version slot
    ext = png(tmp_path / "elsewhere" / "new page.jpg", fmt="JPEG")
    assert run("import", root, "ep01", "page", "p01", ext) == 0
    v2 = ep_root / "pages" / "p01.v2.png"
    with Image.open(v2) as im:
        assert im.format == "PNG"
    assert load_yaml(ep_root / "state.yaml")["pages"]["p01"] == {"page": "pages/p01.v2.png", "panels": {}}


def test_agent_import_panel_does_not_change_state(tmp_path, capsys):
    root = setup_project(tmp_path)
    ep_root = root / "episodes" / "ep01"
    state = {"pages": {"p01": {"page": None, "panels": {}}}}
    save_yaml(ep_root / "state.yaml", state)
    assert run("gen", "panel", root, "ep01", "p01", "1") == 3
    ext = png(tmp_path / "x.png")
    assert run("import", root, "ep01", "panel", "p01", "1", ext) == 0
    assert (ep_root / "panels" / "p01-1.v1.png").is_file()
    assert load_yaml(ep_root / "state.yaml") == state


def test_import_usage_errors(tmp_path, capsys):
    root = setup_project(tmp_path)
    img = png(tmp_path / "x.png")
    assert run("import", root, "page", "p01", img) == 1  # missing EP
    assert run("import", root, "ep01", "page", img) == 1  # missing PAGE
    assert run("import", root, "ep01", "page", "p09", img) == 1  # unknown page
    assert run("import", root, "ep01", "page", "p01", tmp_path / "missing.png") == 1
    bad = tmp_path / "bad.png"
    bad.write_bytes(b"not an image")
    assert run("import", root, "ep01", "page", "p01", bad) == 1
    assert not (root / "episodes" / "ep01" / "pages").exists() or not list((root / "episodes" / "ep01" / "pages").glob("*.png"))


# --------------------------------------------------------------------------- cast commands


def test_cast_new_sheet_approve(tmp_path, capsys):
    root = make_project(tmp_path / "proj")
    assert run("cast", "new", root, "alice", "--name", "앨리스") == 0
    yml = root / "cast" / "alice" / "character.yaml"
    data = yaml.safe_load(yml.read_text(encoding="utf-8"))
    assert data["id"] == "alice" and data["name"] == "앨리스" and data["status"] == "draft"
    assert (root / "cast" / "alice" / "drafts").is_dir()
    assert run("cast", "new", root, "alice", "--name", "X") == 1  # never overwrite
    assert run("cast", "new", root, "bad id", "--name", "X") == 1

    assert run("cast", "approve", root, "alice") == 1  # no drafts yet
    assert run("gen", "sheet", root, "alice") == 3
    draft = root / "cast" / "alice" / "drafts" / "sheet.v1.png"
    assert (root / "cast" / "alice" / "drafts" / "sheet.v1.png.request.yaml").is_file()
    ext = png(tmp_path / "sheet.png", size=(60, 40))
    assert run("import", root, "sheet", "alice", ext) == 0  # EP may be omitted for sheets
    assert draft.is_file()
    png(root / "cast" / "alice" / "drafts" / "sheet.v2.png", size=(90, 60))
    assert run("cast", "approve", root, "alice", "--draft", "drafts/sheet.v1.png") == 0
    ch = Project.load(root).character("alice")
    assert ch.approved and ch.data["sheet"] == "refs/sheet.png"
    with Image.open(ch.sheet_path()) as im:
        assert im.size == (60, 40)
    assert run("cast", "approve", root, "alice") == 0  # default: latest draft
    with Image.open(ch.sheet_path()) as im:
        assert im.size == (90, 60)


FAKE_PS1 = r'''
param([string]$PromptFile, [string[]]$Reference = @(), [string]$Output, [string]$PromptMode)
$Reference = @($Reference | ForEach-Object { $_ -split ',' } | Where-Object { $_ })
Add-Type -AssemblyName System.Drawing
$b = New-Object System.Drawing.Bitmap 20, 30
$b.Save($Output, [System.Drawing.Imaging.ImageFormat]::Png); $b.Dispose()
Write-Output "OK $Output (20x30, 1 bytes, 0s, refs$($Reference.Count)/$PromptMode)"
'''


@pytest.mark.skipif(sys.platform != "win32", reason="needs Windows PowerShell")
def test_codex_relay_through_powershell(tmp_path):
    """The default command line (powershell -File relay.ps1 ...) with a fake .ps1, paths with spaces."""
    from comic.backends import get_backend

    relay = tmp_path / "fake relay.ps1"
    relay.write_text(FAKE_PS1, encoding="utf-8")
    root = setup_project(tmp_path, backend={"name": "codex-relay", "relay": str(relay), "prompt_mode": "Draft"})
    project = Project.load(root)
    prompt = tmp_path / "p.txt"
    prompt.write_text("hello", encoding="utf-8")
    refs = [png(tmp_path / "ref one.png"), png(tmp_path / "ref2.png")]
    backend = get_backend(project)
    out = backend.generate(prompt, refs, tmp_path / "out dir" / "x.v1.png")
    assert out.is_file()
    assert backend.info.endswith("refs2/Draft")
