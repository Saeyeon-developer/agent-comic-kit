"""Tests for the core module (project model, presets, layout, fonts, core commands)."""

from __future__ import annotations

import sys
import types
from pathlib import Path

import pytest
import yaml
from PIL import Image

import comic.commands.core as core_cmd
import comic.presets as presets
from comic import cli
from comic.layout import aspect_text, slot_boxes
from comic.project import ComicError, ComicWaiting, Project, latest_version, next_version, save_yaml

PRESET = {
    "name": "test-slides",
    "label": "Test 4:5",
    "kind": "slides",
    "width": 1080,
    "height": 1350,
    "slice_height": None,
    "max_pages": 20,
    "per_post": None,
    "file": {"type": "jpg", "quality": 92, "max_mb": 8},
    "layout": {"margin": 36, "gutter": 24, "border": 4},
    "checked": "2026-10",
}


@pytest.fixture
def preset_dir(tmp_path, monkeypatch):
    d = tmp_path / "presets"
    d.mkdir()
    save_yaml(d / "test-slides.yaml", PRESET)
    save_yaml(d / "test-scroll.yaml", {**PRESET, "name": "test-scroll", "kind": "scroll", "width": 800, "height": 1280,
                                       "layout": {"margin": 0}})
    monkeypatch.setattr(presets, "PRESETS_DIR", d)
    return d


def make_project(root: Path, preset="test-slides", overrides=None) -> Path:
    root.mkdir(parents=True, exist_ok=True)
    save_yaml(root / "comic.yaml", {
        "title": "Crepe Day", "language": "ko", "reading_direction": "ltr",
        "format": {"preset": preset, "overrides": overrides or {}},
        "style": {"description": "clean line art", "color": "full colour", "refs": []},
        "lettering": {"mode": "overlay", "font": None, "size": 30},
        "backend": {"name": "agent"},
    })
    return root


def add_character(root: Path, cid: str, approved: bool):
    d = root / "cast" / cid
    data = {"id": cid, "name": cid.title(), "status": "draft", "appearance": "x", "inputs": [], "sheet": None}
    if approved:
        (d / "refs").mkdir(parents=True)
        Image.new("RGB", (30, 20), "white").save(d / "refs" / "sheet.png")
        data.update(status="approved", sheet="refs/sheet.png")
    save_yaml(d / "character.yaml", data)


SCRIPT = {
    "episode": "ep01",
    "title": "Crepe Day",
    "pages": [{
        "id": "p01",
        "panels": [
            {"id": 1, "weight": 55, "characters": ["alice", "bob"], "focus": 0.5,
             "dialogue": [{"type": "narration", "text": "Saturday"},
                          {"type": "speech", "speaker": "alice", "text": "Crepes!"}]},
            {"id": 2, "weight": 45, "characters": ["bob"],
             "dialogue": [{"type": "thought", "speaker": "bob", "text": "..."}]},
        ],
    }],
}


def add_episode(root: Path, script=SCRIPT, ep="ep01"):
    save_yaml(root / "episodes" / ep / "script.yaml", script)


# --------------------------------------------------------------------------- versioning


def test_next_version(tmp_path):
    d = tmp_path / "pages"
    assert next_version(d, "p01").name == "p01.v1.png"
    assert d.is_dir()
    for n in (1, 2, 10):
        (d / f"p01.v{n}.png").write_bytes(b"x")
    (d / "p01-1.v99.png").write_bytes(b"x")  # other stem must not count
    (d / "p01.v5.json").write_bytes(b"x")  # other extension must not count
    assert next_version(d, "p01").name == "p01.v11.png"
    assert next_version(d, "p01", ".json").name == "p01.v6.json"
    assert next_version(d, "p01-1").name == "p01-1.v100.png"
    assert latest_version(d, "p01").name == "p01.v10.png"
    assert latest_version(d, "nothing") is None


# --------------------------------------------------------------------------- layout


def test_slot_boxes_rows():
    boxes = slot_boxes(1080, 1350, [55, 45], 36, 24)
    assert len(boxes) == 2
    assert boxes[0][0] == 36 and boxes[0][2] == 1044
    assert boxes[0][1] == 36
    assert boxes[1][1] == boxes[0][3] + 24
    assert boxes[1][3] == 1350 - 36  # last slot absorbs rounding
    avail = 1350 - 72 - 24
    assert boxes[0][3] - boxes[0][1] == round(avail * 0.55)


def test_slot_boxes_rounding_and_single():
    boxes = slot_boxes(1000, 1000, [1, 1, 1], 10, 7)
    assert boxes[-1][3] == 990
    assert sum(b[3] - b[1] for b in boxes) == 1000 - 20 - 14
    assert slot_boxes(800, 1280, [1], 0, 0) == [(0, 0, 800, 1280)]
    with pytest.raises(ValueError):
        slot_boxes(100, 100, [1, 0], 0, 0)


@pytest.mark.parametrize("w,h,expected", [
    (1080, 1350, "4:5"), (1536, 1024, "3:2"), (1080, 1440, "3:4"), (1024, 1024, "1:1"),
    (1920, 1080, "1.78:1"), (1008, 422, "2.39:1"), (422, 1008, "1:2.39"), (800, 1280, "5:8"),
    (1080, 1355, "4:5"), (1008, 300, "10:3"), (1008, 280, "3.60:1"),
])
def test_aspect_text(w, h, expected):
    assert aspect_text(w, h) == expected


# --------------------------------------------------------------------------- presets


def test_preset_merge(preset_dir):
    p = presets.load_preset("test-slides", {"height": 1440, "layout": {"gutter": 10}, "file": {"type": "png"}})
    assert (p["width"], p["height"]) == (1080, 1440)
    assert p["layout"] == {"margin": 36, "gutter": 10, "border": 4}
    assert p["file"] == {"type": "png", "quality": 92, "max_mb": 8}
    # the stored preset is not mutated
    assert presets.load_preset("test-slides")["height"] == 1350
    # missing layout keys get defaults
    assert presets.load_preset("test-scroll")["layout"] == {"margin": 0, "gutter": 24, "border": 4}
    assert [x["name"] for x in presets.list_presets()] == ["test-scroll", "test-slides"]
    with pytest.raises(ComicError, match="unknown preset 'nope'.*test-scroll"):
        presets.load_preset("nope")


def test_project_geometry(tmp_path, preset_dir):
    root = make_project(tmp_path / "proj", overrides={"layout": {"margin": 20}})
    project = Project.load(root)
    assert project.canvas() == (1080, 1350)
    assert project.layout_params() == {"margin": 20, "gutter": 24, "border": 4}
    assert project.path("cast/a.png") == project.root / "cast" / "a.png"
    assert project.validate() == []


# --------------------------------------------------------------------------- project / approval gate


def test_load_errors(tmp_path, preset_dir):
    with pytest.raises(ComicError, match="no comic.yaml"):
        Project.load(tmp_path)
    (tmp_path / "comic.yaml").write_text("title: [unclosed", encoding="utf-8")
    with pytest.raises(ComicError, match="invalid YAML"):
        Project.load(tmp_path)


def test_episode_model(tmp_path, preset_dir):
    root = make_project(tmp_path / "proj")
    add_character(root, "alice", approved=True)
    add_character(root, "bob", approved=True)
    add_episode(root)
    project = Project.load(root)
    ep = project.episode("ep01")
    assert ep.validate() == []
    assert ep.panel_ids("p01") == ["1", "2"]
    assert ep.page_slot_weights("p01") == [55.0, 45.0]
    assert ep.panel("p01", 2)["characters"] == ["bob"]
    assert ep.panel("p01", "2") is ep.panel("p01", 2)
    assert ep.page_characters("p01") == ["alice", "bob"]
    with pytest.raises(ComicError):
        ep.page("p09")
    with pytest.raises(ComicError):
        project.episode("ep02")
    # state and lettering round trip, unicode kept
    assert ep.state() == {"pages": {}}
    ep.save_state({"pages": {"p01": {"page": "pages/p01.v1.png", "panels": {"1": "panels/p01-1.v1.png"}}}})
    assert ep.state()["pages"]["p01"]["panels"]["1"] == "panels/p01-1.v1.png"
    ep.save_lettering({"p01-1#0": {"at": [0.2, 0.1], "width": 0.3, "auto": True}})
    assert ep.lettering()["p01-1#0"]["at"] == [0.2, 0.1]
    ch = project.character("alice")
    ch.data["name"] = "앨리스"
    ch.save()
    text = (root / "cast" / "alice" / "character.yaml").read_text(encoding="utf-8")
    assert "앨리스" in text and text.index("id:") < text.index("name:")


def test_approval_gate(tmp_path, preset_dir):
    root = make_project(tmp_path / "proj")
    add_character(root, "alice", approved=True)
    add_character(root, "bob", approved=False)
    add_episode(root)
    project = Project.load(root)
    assert project.characters()["alice"].approved
    assert not project.characters()["bob"].approved
    problems = project.episode("ep01").validate()
    assert len(problems) == 1 and "bob" in problems[0] and "not approved" in problems[0]

    # status approved but sheet file missing is still not approved
    (root / "cast" / "alice" / "refs" / "sheet.png").unlink()
    project = Project.load(root)
    assert not project.character("alice").approved
    problems = project.episode("ep01").validate()
    assert any("alice" in p for p in problems)
    assert any("sheet file is missing" in p for p in project.validate())


def test_script_validation(tmp_path, preset_dir):
    root = make_project(tmp_path / "proj")
    add_character(root, "alice", approved=True)
    script = {"pages": [
        {"id": "p01", "panels": [
            {"id": 1, "characters": ["ghost"], "weight": 0,
             "dialogue": [{"type": "speech", "text": "hi"}, {"type": "yell", "speaker": "alice", "text": "x"}]},
            {"id": 1, "characters": ["alice"], "focus": 2},
        ]},
        {"id": "p01", "panels": []},
    ]}
    add_episode(root, script)
    problems = "\n".join(Project.load(root).episode("ep01").validate())
    assert "unknown character 'ghost'" in problems
    assert "p01-1#0: speaker is required" in problems
    assert "type 'yell'" in problems
    assert "duplicate panel id '1'" in problems
    assert "duplicate page id 'p01'" in problems
    assert "weight must be a positive number" in problems
    assert "focus must be a number from 0 to 1" in problems
    assert "'panels' must be a non-empty list" in problems


# --------------------------------------------------------------------------- commands


def test_init_and_status(tmp_path, preset_dir, capsys):
    root = tmp_path / "my comic"
    assert cli.main(["init", str(root), "--title", "크레페 데이", "--preset", "test-slides"]) == 0
    cfg = yaml.safe_load((root / "comic.yaml").read_text(encoding="utf-8"))
    assert cfg["title"] == "크레페 데이" and cfg["format"]["preset"] == "test-slides"
    assert (root / "cast").is_dir() and (root / "episodes").is_dir()

    # never overwrite
    (root / "comic.yaml").write_text((root / "comic.yaml").read_text(encoding="utf-8") + "\n# mine\n", encoding="utf-8")
    assert cli.main(["init", str(root), "--title", "Other"]) == 0
    assert "# mine" in (root / "comic.yaml").read_text(encoding="utf-8")
    capsys.readouterr()

    assert cli.main(["status", str(root)]) == 0
    out = capsys.readouterr().out
    assert "크레페 데이" in out and "next: comic cast new" in out

    add_character(root, "alice", approved=True)
    add_character(root, "bob", approved=False)
    add_episode(root)
    assert cli.main(["status", str(root), "ep01"]) == 0
    out = capsys.readouterr().out
    assert "[ ] bob" in out and "not approved" in out
    assert "next: make and approve the sheet of bob" in out

    (root / "cast" / "bob" / "refs").mkdir()
    Image.new("RGB", (4, 4)).save(root / "cast" / "bob" / "refs" / "sheet.png")
    ch = Project.load(root).character("bob")
    ch.data.update(status="approved", sheet="refs/sheet.png")
    ch.save()
    assert cli.main(["status", str(root)]) == 0
    out = capsys.readouterr().out
    assert "panels 0/2" in out and "next: comic gen page" in out

    ep_root = root / "episodes" / "ep01"
    for rel in ("pages/p01.v1.png", "panels/p01-1.v1.png", "panels/p01-2.v1.png"):
        (ep_root / rel).parent.mkdir(parents=True, exist_ok=True)
        Image.new("RGB", (4, 4)).save(ep_root / rel)
    save_yaml(ep_root / "state.yaml", {"pages": {"p01": {"page": "pages/p01.v1.png",
                                                          "panels": {"1": "panels/p01-1.v1.png", 2: "panels/p01-2.v1.png"}}}})
    assert cli.main(["status", str(root)]) == 0
    out = capsys.readouterr().out
    assert "panels 2/2" in out and "comic compose" in out


def test_init_without_template(tmp_path, preset_dir, monkeypatch, capsys):
    monkeypatch.setattr(core_cmd, "TEMPLATES_DIR", tmp_path / "no-templates")
    root = tmp_path / "proj"
    assert cli.main(["init", str(root)]) == 0
    cfg = yaml.safe_load((root / "comic.yaml").read_text(encoding="utf-8"))
    assert cfg["title"] == "proj" and cfg["format"]["preset"] == "instagram-carousel"
    assert cfg["backend"]["name"] == "codex-relay"


def test_init_unknown_preset(tmp_path, preset_dir, capsys):
    assert cli.main(["init", str(tmp_path / "p"), "--preset", "nope"]) == 1
    err = capsys.readouterr().err
    assert err.startswith("error: unknown preset") and err.count("\n") == 1


def test_presets_and_doctor(tmp_path, preset_dir, capsys):
    assert cli.main(["presets"]) == 0
    out = capsys.readouterr().out
    assert "test-slides  slides  1080x1350 (4:5)  checked 2026-10" in out
    root = make_project(tmp_path / "proj")
    code = cli.main(["doctor", str(root)])
    out = capsys.readouterr().out
    assert "python:" in out and "font:" in out and "backend: agent" in out
    assert code in (0, 1)


def test_cli_module_loading_and_exit_codes(monkeypatch, capsys):
    fake = types.ModuleType("comic_test_fake_cmds")

    class Waiting(ComicWaiting):
        pass

    def register(sub):
        p = sub.add_parser("wait")
        p.set_defaults(func=lambda a: (_ for _ in ()).throw(Waiting("save the image to X, then run comic import ...")))
        p = sub.add_parser("fail")
        p.set_defaults(func=lambda a: (_ for _ in ()).throw(ComicError("bad\nthing")))

    fake.register = register
    monkeypatch.setitem(sys.modules, "comic_test_fake_cmds", fake)
    monkeypatch.setattr(cli, "COMMAND_MODULES", ("comic.commands.core", "comic.commands.does_not_exist", "comic_test_fake_cmds"))
    assert cli.main(["wait"]) == 3
    assert "comic import" in capsys.readouterr().out
    assert cli.main(["fail"]) == 1
    assert capsys.readouterr().err == "error: bad thing\n"

    # a module that exists but fails on its own import must not be swallowed
    monkeypatch.setattr(cli, "COMMAND_MODULES", ("comic.commands.core", "comic_test_broken_missing_dep"))
    monkeypatch.setattr(cli.importlib, "import_module", lambda name: (_ for _ in ()).throw(
        ModuleNotFoundError("No module named 'some_dep'", name="some_dep")) if name == "comic_test_broken_missing_dep"
        else sys.modules[name])
    with pytest.raises(ModuleNotFoundError):
        cli.build_parser()


def test_find_font(tmp_path):
    from comic.fonts import find_font

    f = tmp_path / "my.ttf"
    f.write_bytes(b"x")
    assert find_font("ko", f) == f
    try:
        found = find_font("ko")
    except ComicError as e:
        assert "searched" in str(e)
    else:
        assert found.is_file()
