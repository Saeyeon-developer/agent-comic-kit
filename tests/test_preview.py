import importlib.util
from pathlib import Path

from PIL import Image

try:
    from comic.preview import write_preview
except ImportError:  # comic package not importable yet: load the file directly
    _path = Path(__file__).resolve().parent.parent / "comic" / "preview.py"
    _spec = importlib.util.spec_from_file_location("comic_preview", _path)
    _mod = importlib.util.module_from_spec(_spec)
    _spec.loader.exec_module(_mod)
    write_preview = _mod.write_preview


def _make(path: Path, size=(40, 50)) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    Image.new("RGB", size, (200, 120, 90)).save(path)
    return path


def _check(html_path: Path, out_dir: Path, files):
    assert html_path.exists()
    assert html_path == out_dir / "preview.html"
    text = html_path.read_text(encoding="utf-8")
    for f in files:
        assert f.relative_to(out_dir).as_posix() in text
    assert "http" not in text
    return text


def test_slides(tmp_path):
    files = [_make(tmp_path / ("%02d.png" % i)) for i in range(1, 4)]
    preset = {"kind": "slides", "width": 1080, "height": 1350, "per_post": None}
    text = _check(write_preview(tmp_path, files, preset, "My <Comic> & Co"), tmp_path, files)
    assert "My &lt;Comic&gt; &amp; Co" in text
    assert "<Comic>" not in text
    assert "1080 / 1350" in text
    assert "ArrowLeft" in text and "ArrowRight" in text
    assert "1 / 3" in text
    assert "Post 1" not in text


def test_per_post(tmp_path):
    files = [
        _make(tmp_path / "post-1" / "01.png"),
        _make(tmp_path / "post-1" / "02.png"),
        _make(tmp_path / "post-2" / "03.png"),
    ]
    preset = {"kind": "slides", "width": 1080, "height": 1350, "per_post": 2}
    text = _check(write_preview(tmp_path, files, preset, "X thread"), tmp_path, files)
    assert "Post 1" in text and "Post 2" in text
    assert text.count('class="carousel"') == 2
    assert "1 / 2" in text and "1 / 1" in text


def test_scroll(tmp_path):
    files = [_make(tmp_path / ("%02d.png" % i), (40, 80)) for i in range(1, 3)]
    preset = {"kind": "scroll", "width": 800, "height": 1280, "slice_height": 1280}
    text = _check(write_preview(tmp_path, files, preset, "Strip"), tmp_path, files)
    assert "max-width: 800px" in text
    assert 'class="carousel"' not in text
