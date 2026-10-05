"""Static HTML preview for exported comic files.

write_preview() writes a single self-contained out_dir/preview.html: inline CSS
and JS, images referenced by path relative to out_dir, no network access.
Standard library only.
"""

from __future__ import annotations

import html
import os
from pathlib import Path
from urllib.parse import quote

_CSS = """
:root {
  --bg: #f4f4f5; --fg: #18181b; --muted: #71717a; --panel: #e4e4e7;
  --btn: rgba(255,255,255,.88); --btn-fg: #18181b; --dot: #a1a1aa; --dot-on: #18181b;
}
@media (prefers-color-scheme: dark) {
  :root {
    --bg: #121214; --fg: #f4f4f5; --muted: #a1a1aa; --panel: #1f1f23;
    --btn: rgba(30,30,34,.88); --btn-fg: #f4f4f5; --dot: #52525b; --dot-on: #f4f4f5;
  }
}
* { box-sizing: border-box; }
html, body { margin: 0; padding: 0; }
body {
  background: var(--bg); color: var(--fg);
  font: 15px/1.5 system-ui, -apple-system, "Segoe UI", Roboto, "Noto Sans", sans-serif;
  overflow-x: hidden;
}
main { padding: 16px; }
h1 { font-size: 1.15rem; margin: 0 auto 16px; text-align: center; max-width: 420px; }
h2 { font-size: .95rem; margin: 28px auto 10px; max-width: 420px; color: var(--muted); font-weight: 600; }
.carousel { max-width: 420px; margin: 0 auto 8px; }
.frame {
  position: relative; width: 100%; background: var(--panel);
  border-radius: 6px; overflow: hidden;
}
.track {
  position: absolute; inset: 0; display: flex; overflow-x: auto; overflow-y: hidden;
  scroll-snap-type: x mandatory; scrollbar-width: none; -ms-overflow-style: none;
  overscroll-behavior-x: contain;
}
.track::-webkit-scrollbar { display: none; }
.slide { flex: 0 0 100%; height: 100%; scroll-snap-align: center; scroll-snap-stop: always; }
.slide img { display: block; width: 100%; height: 100%; object-fit: contain; user-select: none; -webkit-user-drag: none; }
.nav {
  position: absolute; top: 50%; transform: translateY(-50%); width: 36px; height: 36px;
  border: 0; border-radius: 50%; background: var(--btn); color: var(--btn-fg);
  font-size: 22px; line-height: 1; cursor: pointer; z-index: 2;
  box-shadow: 0 1px 4px rgba(0,0,0,.25);
}
.nav:disabled { opacity: 0; pointer-events: none; }
.prev { left: 8px; } .next { right: 8px; }
.bar { display: flex; align-items: center; justify-content: space-between; padding: 10px 2px 0; }
.dots { display: flex; gap: 6px; flex-wrap: wrap; }
.dot { width: 8px; height: 8px; padding: 0; border: 0; border-radius: 50%; background: var(--dot); cursor: pointer; }
.dot.on { background: var(--dot-on); }
.count { color: var(--muted); font-variant-numeric: tabular-nums; }
.scroll { margin: 0 auto; width: 100%; }
.scroll img { display: block; width: 100%; height: auto; margin: 0; }
.hint { text-align: center; color: var(--muted); font-size: .8rem; margin-top: 16px; }
"""

_JS = """
(function () {
  var carousels = Array.prototype.slice.call(document.querySelectorAll('.carousel'));
  var active = null;

  function setup(c) {
    var track = c.querySelector('.track');
    var n = track.children.length;
    var prev = c.querySelector('.prev');
    var next = c.querySelector('.next');
    var dots = c.querySelectorAll('.dot');
    var count = c.querySelector('.count');
    var cur = 0;

    function width() { return track.clientWidth || 1; }
    function show(i) {
      i = Math.max(0, Math.min(n - 1, i));
      track.scrollTo({ left: i * width(), behavior: 'smooth' });
    }
    function update() {
      cur = Math.max(0, Math.min(n - 1, Math.round(track.scrollLeft / width())));
      count.textContent = (cur + 1) + ' / ' + n;
      prev.disabled = cur === 0;
      next.disabled = cur === n - 1;
      for (var k = 0; k < dots.length; k++) dots[k].classList.toggle('on', k === cur);
    }
    c._go = function (d) { show(cur + d); };
    prev.addEventListener('click', function () { active = c; show(cur - 1); });
    next.addEventListener('click', function () { active = c; show(cur + 1); });
    for (var k = 0; k < dots.length; k++) {
      (function (j) { dots[j].addEventListener('click', function () { active = c; show(j); }); })(k);
    }
    track.addEventListener('scroll', update, { passive: true });
    window.addEventListener('resize', function () {
      track.scrollTo({ left: cur * width() });
      update();
    });
    ['pointerdown', 'touchstart', 'mouseenter', 'focusin'].forEach(function (ev) {
      c.addEventListener(ev, function () { active = c; }, { passive: true });
    });
    update();
  }

  function mostVisible() {
    var best = null, bestArea = -1, vh = window.innerHeight;
    carousels.forEach(function (c) {
      var r = c.getBoundingClientRect();
      var area = Math.max(0, Math.min(r.bottom, vh) - Math.max(r.top, 0));
      if (area > bestArea) { bestArea = area; best = c; }
    });
    return best;
  }

  carousels.forEach(setup);

  document.addEventListener('keydown', function (e) {
    if (e.key !== 'ArrowLeft' && e.key !== 'ArrowRight') return;
    if (e.altKey || e.ctrlKey || e.metaKey || e.shiftKey) return;
    var c = active || mostVisible();
    if (!c) return;
    e.preventDefault();
    c._go(e.key === 'ArrowLeft' ? -1 : 1);
  });
})();
"""


def _rel(out_dir: Path, f: Path) -> str:
    """Path of f relative to out_dir, with forward slashes, URL-quoted."""
    try:
        rel = os.path.relpath(f, out_dir)
    except ValueError:  # different drive on Windows
        rel = Path(f).name
    return quote(rel.replace(os.sep, "/"), safe="/")


def _group_files(files: list[Path], per_post: int) -> list[list[Path]]:
    """Group files by parent folder name (post-1/, post-2/, ...), keeping order.

    If everything sits in one folder, fall back to chunks of per_post.
    """
    groups: dict[str, list[Path]] = {}
    for f in files:
        groups.setdefault(Path(f).parent.name, []).append(f)
    if len(groups) <= 1 and len(files) > per_post:
        return [files[i:i + per_post] for i in range(0, len(files), per_post)]
    return list(groups.values())


def _carousel(out_dir: Path, files: list[Path], aspect: str, label: str) -> str:
    n = len(files)
    slides = "".join(
        '<div class="slide"><img src="%s" alt="%s" draggable="false"></div>'
        % (_rel(out_dir, f), html.escape("%s, slide %d" % (label, i + 1), quote=True))
        for i, f in enumerate(files)
    )
    dots = "".join(
        '<button class="dot" type="button" aria-label="Go to slide %d"></button>' % (i + 1)
        for i in range(n)
    )
    return (
        '<section class="carousel">'
        '<div class="frame" style="aspect-ratio: %s">'
        '<div class="track">%s</div>'
        '<button class="nav prev" type="button" aria-label="Previous">&#8249;</button>'
        '<button class="nav next" type="button" aria-label="Next">&#8250;</button>'
        "</div>"
        '<div class="bar"><div class="dots">%s</div><div class="count">1 / %d</div></div>'
        "</section>" % (aspect, slides, dots, n)
    )


def write_preview(out_dir: Path, files: list[Path], preset: dict, title: str) -> Path:
    """Write out_dir/preview.html for the exported files and return its path."""
    out_dir = Path(out_dir)
    out_dir.mkdir(parents=True, exist_ok=True)
    files = [Path(f) for f in files]
    safe_title = html.escape(str(title), quote=True)
    width = int(preset.get("width") or 1080)
    height = int(preset.get("height") or 1350)

    parts: list[str] = []
    if preset.get("kind") == "scroll":
        imgs = "".join(
            '<img src="%s" alt="%s" loading="lazy">'
            % (_rel(out_dir, f), html.escape("Part %d" % (i + 1), quote=True))
            for i, f in enumerate(files)
        )
        parts.append('<div class="scroll" style="max-width: %dpx">%s</div>' % (width, imgs))
    else:
        aspect = "%d / %d" % (width, height)
        per_post = preset.get("per_post")
        if per_post:
            for i, group in enumerate(_group_files(files, int(per_post)), 1):
                parts.append("<h2>Post %d</h2>" % i)
                parts.append(_carousel(out_dir, group, aspect, "Post %d" % i))
        else:
            parts.append(_carousel(out_dir, files, aspect, str(title)))
        parts.append('<p class="hint">Use the buttons, arrow keys or swipe to turn pages.</p>')

    doc = (
        "<!doctype html>\n"
        '<html lang="en">\n<head>\n<meta charset="utf-8">\n'
        '<meta name="viewport" content="width=device-width, initial-scale=1">\n'
        '<meta name="color-scheme" content="light dark">\n'
        "<title>%s</title>\n<style>%s</style>\n</head>\n<body>\n<main>\n"
        "<h1>%s</h1>\n%s\n</main>\n<script>%s</script>\n</body>\n</html>\n"
        % (safe_title, _CSS, safe_title, "\n".join(parts), _JS)
    )
    out = out_dir / "preview.html"
    out.write_text(doc, encoding="utf-8")
    return out
