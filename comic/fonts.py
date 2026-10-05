"""Font discovery for lettering."""

from __future__ import annotations

import os
import sys
from pathlib import Path

from comic.project import ComicError

# Noto families, preferred per dialogue language (first match wins).
_NOTO = {
    "ko": ["NotoSansKR-VF.ttf", "NotoSansKR-Regular.ttf", "NotoSansKR-Regular.otf", "NotoSansCJKkr-Regular.otf",
           "NotoSansCJK-Regular.ttc", "NotoSansKR[wght].ttf"],
    "ja": ["NotoSansJP-VF.ttf", "NotoSansJP-Regular.ttf", "NotoSansJP-Regular.otf", "NotoSansCJKjp-Regular.otf",
           "NotoSansCJK-Regular.ttc", "NotoSansJP[wght].ttf"],
    "zh": ["NotoSansSC-VF.ttf", "NotoSansSC-Regular.ttf", "NotoSansSC-Regular.otf", "NotoSansCJKsc-Regular.otf",
           "NotoSansCJK-Regular.ttc", "NotoSansSC[wght].ttf", "NotoSansTC-VF.ttf", "NotoSansCJKtc-Regular.otf"],
}
_NOTO_ANY = ["NotoSansCJK-Regular.ttc", "NotoSansKR-VF.ttf", "NotoSansJP-VF.ttf", "NotoSansSC-VF.ttf",
             "NotoSansCJKkr-Regular.otf", "NotoSansCJKjp-Regular.otf", "NotoSansCJKsc-Regular.otf"]
# OS default fonts, per language, then generic fallbacks.
_OS = {
    "ko": ["malgun.ttf", "AppleSDGothicNeo.ttc", "NanumGothic.ttf", "UnDotum.ttf"],
    "ja": ["meiryo.ttc", "YuGothR.ttc", "msgothic.ttc", "HiraginoSans-W3.ttc", "ヒラギノ角ゴシック W3.ttc"],
    "zh": ["msyh.ttc", "simhei.ttf", "PingFang.ttc", "wqy-microhei.ttc"],
}
_GENERIC = ["NotoSans-Regular.ttf", "segoeui.ttf", "arial.ttf", "Arial.ttf", "Helvetica.ttc", "DejaVuSans.ttf"]


def font_dirs() -> list[Path]:
    dirs: list[Path] = []
    if sys.platform == "win32":
        windir = os.environ.get("WINDIR", r"C:\Windows")
        dirs.append(Path(windir) / "Fonts")
        local = os.environ.get("LOCALAPPDATA")
        if local:
            dirs.append(Path(local) / "Microsoft" / "Windows" / "Fonts")
    elif sys.platform == "darwin":
        dirs += [Path("/System/Library/Fonts"), Path("/System/Library/Fonts/Supplemental"),
                 Path("/Library/Fonts"), Path.home() / "Library" / "Fonts"]
    else:
        dirs += [Path("/usr/share/fonts"), Path("/usr/local/share/fonts"),
                 Path.home() / ".fonts", Path.home() / ".local" / "share" / "fonts"]
    return dirs


def candidates(language: str | None) -> list[str]:
    lang = (language or "").lower().split("-")[0].split("_")[0]
    names = _NOTO.get(lang, []) + _NOTO_ANY + _OS.get(lang, [])
    for other in ("ko", "ja", "zh"):
        if other != lang:
            names += _OS[other]
    names += _GENERIC
    seen: set[str] = set()
    return [n for n in names if not (n.lower() in seen or seen.add(n.lower()))]


def _index(dirs: list[Path]) -> dict[str, Path]:
    """lowercase file name -> path; top level first, then subfolders (Linux layout)."""
    index: dict[str, Path] = {}
    for d in dirs:
        if not d.is_dir():
            continue
        try:
            for p in d.iterdir():
                if p.is_file():
                    index.setdefault(p.name.lower(), p)
            for p in d.rglob("*"):
                if p.suffix.lower() in (".ttf", ".otf", ".ttc"):
                    index.setdefault(p.name.lower(), p)
        except OSError:
            continue
    return index


def find_font(language: str | None, override=None) -> Path:
    """Font file for the dialogue language.

    `override` is used when it exists; otherwise Noto Sans CJK/KR/JP/SC, then
    OS defaults (Malgun Gothic, Apple SD Gothic Neo, Meiryo, ...).
    """
    if override:
        p = Path(override).expanduser()
        if p.is_file():
            return p
    dirs = font_dirs()
    index = _index(dirs)
    names = candidates(language)
    for name in names:
        hit = index.get(name.lower())
        if hit:
            return hit
    searched = ", ".join(str(d) for d in dirs)
    bad = f"font override {override} not found; " if override else ""
    raise ComicError(
        f"{bad}no usable font for language '{language}'; searched {searched} for {', '.join(names[:8])}, ...; "
        "set lettering.font in comic.yaml to a .ttf/.otf path"
    )
