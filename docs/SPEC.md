# agent-comic-kit — implementation spec (v0.1)

This is the contract for implementers. Product decisions and rationale live in `PLAN.md` (Korean). The imaging code was ported from early experiment scripts that are not part of this repository.

## 0. Ground rules

- Python ≥ 3.11, dependencies: Pillow, PyYAML only. UTF-8 for every file read/write. `pathlib` everywhere. Windows is the primary platform; nothing may assume POSIX.
- Run as `python -m comic <command>` (no install needed) or `comic <command>` after `pip install -e .`.
- Exit codes: `0` ok, `1` error (message on stderr, one line, actionable), `3` waiting for the agent (agent backend, see §6).
- Output: short plain lines meant to be read by an AI agent. Always print the paths of files written.
- Never overwrite generated images: versions are `name.vN.png` (N = 1, 2, …). Tool-owned state files may be rewritten.
- All coordinates stored in project files are **fractions (0..1) of the panel slot**, never pixels, so they survive re-layout and preset changes.
- Never call the real image generator in tests.

## 1. Repository layout and ownership

```
agent-comic-kit/
├─ pyproject.toml                 [A]  name "agent-comic-kit", script "comic = comic.cli:main"
├─ comic/
│  ├─ __init__.py  __main__.py    [A]
│  ├─ cli.py                      [A]  argparse; calls register() of each commands module
│  ├─ project.py                  [A]  data model, loading, validation, paths, versioning
│  ├─ presets.py                  [A]  preset loading/merging
│  ├─ layout.py                   [A]  slot geometry shared by prompts and compose
│  ├─ fonts.py                    [A]  font discovery
│  ├─ commands/core.py            [A]  init, presets, status, doctor
│  ├─ prompts.py                  [C]  prompt assembly (sheet, page, panel)
│  ├─ backends/__init__.py        [C]  get_backend(project)
│  ├─ backends/codex_relay.py     [C]
│  ├─ backends/agent.py           [C]
│  ├─ commands/generate.py        [C]  cast new/approve, prompt, gen, import
│  ├─ imaging/split.py            [B]
│  ├─ imaging/compose.py          [B]
│  ├─ imaging/letter.py           [B]
│  ├─ imaging/export.py           [B]
│  ├─ commands/imaging.py         [B]  split, use, compose, letter, export
│  └─ preview.py                  [D]  static HTML preview
├─ presets/formats/*.yaml         [D]
├─ templates/{comic,character,script}.yaml   [D]
├─ tests/                         each owner adds tests for their modules
├─ SKILL.md, README.md            [Director]
└─ docs/
```

Owners: A = core (implementer), B = imaging (implementer), C = generation (implementer), D = builder. Only edit files you own. If you need something from another module, use the API in this spec; if it is missing, report it instead of editing someone else's file.

## 2. Project folder (what users and agents edit)

```
my-comic/
├─ comic.yaml
├─ cast/<id>/
│  ├─ character.yaml
│  ├─ inputs/          user-provided source images (optional)
│  ├─ drafts/          sheet.v1.png, sheet.v2.png, … (+ .json sidecars from the backend)
│  └─ refs/sheet.png   approved sheet (copied on approve)
└─ episodes/<ep>/
   ├─ script.yaml      storyboard (agent/user edited)
   ├─ lettering.yaml   bubble placement (tool writes auto placements, agent edits)
   ├─ state.yaml       tool-owned: current versions
   ├─ prompts/         p01.page.txt, p01.page.refs.yaml, p01-2.panel.txt, …
   ├─ pages/           p01.v1.png …
   ├─ panels/          p01-1.v1.png …
   ├─ composed/        p01.png, p01.layout.json
   ├─ lettered/        p01.png
   └─ out/             final files + preview.html
```

### 2.1 comic.yaml

```yaml
title: "Crepe Day"
language: ko                 # dialogue language (font discovery uses it)
reading_direction: ltr       # ltr | rtl | ttb
format:
  preset: instagram-carousel
  overrides: {}              # any preset key, deep-merged over the preset
style:
  description: "Japanese anime fan-art style, full colour, clean line art, cel shading with soft gradients"
  color: "full colour"
  refs: []                   # optional style reference images (project-relative)
lettering:
  mode: overlay              # overlay | in-image (in-image: prompts ask the model to draw bubbles+text)
  font: null                 # path; null = auto-discover (fonts.py)
  size: 30                   # px at a 1080 px wide canvas; scaled by canvas_width/1080
backend:
  name: codex-relay          # codex-relay | agent
  relay: null                # path to imagen.ps1; null = env COMIC_RELAY
  prompt_mode: Final         # passed to the relay
```

### 2.2 cast/<id>/character.yaml

```yaml
id: alice
name: "Alice"
status: draft                # draft | approved  (only approve sets approved)
appearance: >-               # fixed description pasted into every prompt that shows this character
  tall adult woman, short messy blonde bob, long side bangs covering one eye, light-blue eyes, ...
personality: "bright, outgoing, physically affectionate"
height: "tall; clearly taller than Bob"   # free text, used in height notes
expressions: [big grin, playful wink, surprised, pouting]   # for the sheet prompt
inputs: []                   # cast-relative source images used as sheet references
sheet: null                  # set by approve, e.g. refs/sheet.png
```

### 2.3 episodes/<ep>/script.yaml

```yaml
episode: ep01
title: "Crepe Day"
pages:
  - id: p01
    panels:                  # v0.1 layout: panels stacked top→bottom, full width ("rows")
      - id: 1
        weight: 55           # relative slot height (default 1)
        shot: "medium full shot, eye level"
        characters: [alice, bob]
        action: "Alice hugs Bob's shoulders and points at a crepe stand; Bob startled, blushing"
        background: "bright shopping street, daytime"
        bubble_space: "top-left"      # where to keep empty for bubbles (free text)
        focus: 0.5                    # optional crop anchor for compose (0 top … 1 bottom)
        locked: [shot, action]        # fields the USER decided; agents must not change them
        positions:                    # optional, set after looking at the panel image: face centres,
          alice: [0.58, 0.16]        # fractions of the panel; used by `comic letter` auto placement
          bob: [0.33, 0.33]
        dialogue:
          - {type: narration, text: "Saturday afternoon"}
          - {type: speech, speaker: alice, text: "Bob! A new crepe place opened!"}
```

`type` ∈ `speech | shout | thought | narration`. `speaker` required except for narration.

`positions` (optional) maps any character id (a non-cast name such as `clerk` is fine) to the `[x, y]` centre of that character's face in the composed panel slot, as fractions. It is written by the agent after looking at the composed page; the generator never reads it.

### 2.4 episodes/<ep>/lettering.yaml (tool + agent)

Key = `<page>-<panel>#<dialogue index>`.

```yaml
p01-1#0: {at: [0.17, 0.06], width: 0.30}
p01-1#1: {at: [0.80, 0.12], width: 0.24, tail: [0.57, 0.18], size: 28, auto: true}
```

`at` = bubble centre, `tail` = point the tail aims at, `width` = max text line width (default 0.24); all fractions of the slot. `auto: true` marks a placement the tool guessed; the agent removes it after checking. Optional `size` (px at 1080 width), `clamp: true` (keep shapes inside the panel border).

The tail is drawn from the final bubble position (after `clamp`). When the `tail` point lies inside the bubble or closer than a minimum distance to its edge (24 px at 1080 width; 40 px for thought), no tail is drawn and `comic letter` warns `KEY: tail target is inside/too close to the bubble; tail skipped - move 'tail' or 'at'`.

### 2.5 episodes/<ep>/state.yaml (tool-owned)

```yaml
pages:
  p01:
    page: pages/p01.v2.png          # current page image
    panels: {"1": panels/p01-1.v1.png, "2": panels/p01-2.v3.png}   # current panel images
```

## 3. Presets (`presets/formats/*.yaml`) [D]

```yaml
name: instagram-carousel
label: "Instagram carousel 4:5"
kind: slides                 # slides | scroll
width: 1080
height: 1350                 # page canvas; for scroll: height of one page segment
slice_height: null           # scroll only: export slices of this height
max_pages: 20                # slides per post (null = no limit)
per_post: null               # e.g. 4 for X; export groups files into post-1/, post-2/
file: {type: jpg, quality: 92, max_mb: 8}
layout: {margin: 36, gutter: 24, border: 4}   # px at this width
checked: "2026-10"
notes: "All slides must share one aspect ratio."
sources: ["https://..."]
```

Ship: `instagram-carousel` (1080×1350), `instagram-carousel-34` (1080×1440), `x-post` (1080×1350, per_post 4, max_pages null), `vertical-scroll` (width 800, height 1280, slice_height 1280). These are examples, not definitions of any genre.

## 4. Core [A]

`project.py`:

```python
class Project:
    root: Path
    config: dict                       # comic.yaml
    @classmethod
    def load(cls, root) -> "Project"   # raises ComicError with a clear message
    def preset(self) -> dict           # preset merged with format.overrides
    def characters(self) -> dict[str, "Character"]
    def character(self, cid) -> "Character"
    def episode(self, ep) -> "Episode"
    def validate(self) -> list[str]    # human-readable problems (empty = ok)

class Character:
    id: str; root: Path; data: dict    # character.yaml
    @property
    def approved(self) -> bool         # status == approved and sheet file exists
    def sheet_path(self) -> Path | None
    def save(self)

class Episode:
    project: Project; id: str; root: Path
    script: dict                       # script.yaml
    def pages(self) -> list[dict]
    def page(self, page_id) -> dict
    def panel(self, page_id, panel_id) -> dict
    def state(self) -> dict;  def save_state(self, state)
    def lettering(self) -> dict; def save_lettering(self, data)
    def validate(self) -> list[str]    # unknown/unapproved characters, duplicate ids, missing speaker, bad types

class ComicError(Exception): ...

def next_version(directory: Path, stem: str, ext=".png") -> Path   # stem.v{N}.png with N = max+1
```

Gate: `Episode.validate()` reports every character used in a panel that is not approved. Commands that generate pages refuse to run while it reports problems.

`layout.py`:

```python
def slot_boxes(canvas_w, canvas_h, weights, margin, gutter) -> list[tuple[int,int,int,int]]
    # rows layout: full content width, heights ∝ weights; last slot absorbs rounding
def aspect_text(w, h) -> str          # reduced ratio for prompts, e.g. (1080,1350) -> "4:5", (1008,422) -> "2.39:1"
```

`fonts.py`: `find_font(language, override=None) -> Path` — override if it exists; otherwise search known OS font dirs for, in order, Noto Sans CJK/KR/JP/SC (incl. `NotoSansKR-VF.ttf`), then OS defaults (Malgun Gothic, Apple SD Gothic Neo, Meiryo, …). Raise ComicError listing what was searched.

`commands/core.py`:
- `comic init DIR [--title T] [--preset NAME]` — create folders and copy `templates/comic.yaml`; never overwrite an existing comic.yaml.
- `comic presets` — list name, label, kind, size, checked.
- `comic status DIR [EP]` — checklist: cast (approved?), episode validation problems, per page: page version, panels split (n/expected), composed, lettered, exported. End with a one-line "next step" hint.
- `comic doctor [DIR]` — Python version, Pillow/PyYAML, font found, relay script found (if backend codex-relay).

`cli.py`: build argparse with subcommands; `from comic.commands import core, generate, imaging`; each exposes `register(subparsers)`; handlers receive `args`; convert `ComicError` to exit 1.

## 5. Generation [C]

### 5.1 Prompt assembly (`prompts.py`)

Prompts are plain English text assembled from project data. The agent may edit the written prompt file before generating; `gen` sends the file as-is. Never use genre words ("manga", "webtoon", "manhwa") as style instructions; style comes only from `style.description`/`style.color`.

- `sheet_prompt(project, character) -> (text, refs)` — landscape 3:2: full-body front, side, back (left 2/3) + 2×2 shoulder-up expressions from `expressions` (right 1/3), plain light background, no text/labels. Refs = `inputs` (each described as "source image k: carry over design only, not render style/background/pose").
- `page_prompt(project, episode, page_id) -> (text, refs)`:
  1. "One comic page with N panels. The whole image is exactly {aspect_text(preset w,h)}."
  2. Reference roles: one line per attached image ("Image k: character sheet of NAME — keep face, hair, outfit, accessories and body type exactly; do not copy the sheet's background, layout or expression grid"), then style refs.
  3. Characters: `NAME: appearance`, then a height line built from `height` fields of characters on the page.
  4. Layout: thin even white outer margin; panels stacked top to bottom, full width, thin black border, white gutters, nothing crossing borders; relative heights from weights as percentages.
  5. One block per panel: shot, action, background, "keep the {bubble_space} area simple and empty for speech bubbles".
  6. Style: description + colour.
  7. lettering.mode overlay → "No speech bubbles, text, sound effects, readable signs or watermarks." in-image → include the dialogue with bubble types instead.
  Refs = approved sheets of every character on the page (stable order of first appearance) + style refs.
- `panel_prompt(project, episode, page_id, panel_id) -> (text, refs)`: "Redraw a single comic panel. The whole image is exactly {aspect of its slot}." Refs = current page image first ("match line art, colouring and lighting; do not copy other panels' composition"), then sheets of the panel's characters. No border.

The slot aspect comes from `layout.slot_boxes` with the preset's canvas and layout values.

`comic prompt sheet|page|panel …` writes `prompts/<name>.txt` and `prompts/<name>.refs.yaml` (list of project-relative ref paths) and prints both paths. Sheet prompts go to `cast/<id>/drafts/sheet.prompt.txt` (+ refs).

### 5.2 Backends

```python
class Backend:
    def generate(self, prompt_file: Path, refs: list[Path], output: Path) -> Path   # returns written image
```

- `codex_relay`: runs `powershell -NoProfile -ExecutionPolicy Bypass -File <relay> -PromptFile <f> -Output <out> -PromptMode <mode> [-Reference a,b,…]` with a 900 s timeout; non-zero exit → ComicError with the relay's last lines. Relay path: `backend.relay` → env `COMIC_RELAY` → error explaining how to set it (link: https://github.com/Saeyeon-developer/codex-image-relay).
- `agent`: writes `<output>.request.yaml` (prompt file, refs, output path) and raises `AgentActionRequired` → CLI prints the request (prompt path, refs, where to save) and the `comic import …` command to run afterwards, exit code 3.

### 5.3 Commands (`commands/generate.py`)

- `comic cast new DIR ID --name NAME` — create `cast/ID/` from `templates/character.yaml`.
- `comic prompt sheet DIR ID` / `comic gen sheet DIR ID` → `drafts/sheet.vN.png`.
- `comic cast approve DIR ID [--draft PATH]` — copy (default: latest draft) to `refs/sheet.png`, set `status: approved`, `sheet: refs/sheet.png`.
- `comic prompt page DIR EP PAGE` / `comic gen page DIR EP PAGE` → `pages/PAGE.vN.png`, state `page` updated. `gen` uses the existing prompt file if present, else assembles one first. Refuses if `Episode.validate()` has problems.
- `comic prompt panel DIR EP PAGE PANEL` / `comic gen panel …` → `panels/PAGE-PANEL.vN.png` (does **not** become current; the agent reviews, then runs `comic use`).
- `comic import DIR EP (page PAGE | panel PAGE PANEL | sheet ID) IMAGE` — copy an externally generated image into the next version slot (agent backend).

## 6. Imaging [B]

Port the experiment tools; keep their behaviour unless stated.

- `imaging/split.py`: `detect_panels(img, white=235, min_gutter=8) -> list[box]` (recursive XY-cut, reading order top→bottom then by `reading_direction` horizontally), `trim_border(img) -> img` (strip the drawn dark panel border). `comic split DIR EP PAGE [--boxes "x0,y0,x1,y1;…"]`: on the current page; count must equal the script's panel count, else exit 1 printing detected boxes and the `--boxes` hint. Writes `panels/PAGE-ID.vN.png` (border-trimmed) and sets them current.
- `comic use DIR EP PAGE PANEL IMAGE` — make IMAGE (usually a `gen panel` result) the current panel image (copy into `panels/` as next version if it lives elsewhere).
- `imaging/compose.py`: `compose(panels, preset, weights, focuses) -> (Image, boxes)`; cover-fit each panel into its slot (crop anchor = `focus`, default 0.5), draw `layout.border`. `comic compose DIR EP [PAGE…]` → `composed/PAGE.png` + `composed/PAGE.layout.json` (`{"canvas":[w,h],"slots":{"1":[x0,y0,x1,y1],…}}`). Print each slot's crop % so the agent notices heavy crops.
- `imaging/letter.py`: bubbles `speech | shout | thought | narration` as in the experiment (3× supersampling, Noto weights 600/900/500/500). Improvements: balanced line wrapping (lines of similar length; break at spaces; break inside a word only if unavoidable), `clamp` option, tails never longer than ~90 px at 1080 width. `comic letter DIR EP [PAGE…] [--debug]`: reads `composed/PAGE.png` + layout + `lettering.yaml`; dialogue without a placement gets an automatic one, saved back with `auto: true` (default width 0.24 of the slot). Without panel `positions`: packed in rows inside `bubble_space` if parseable — top/bottom × left/right/centre — else top area; ordered by `reading_direction`. With `positions` (§2.3): a face box of 0.16 × panel height on each side of every listed face centre (clipped to the panel) is never covered; a speaker with a position gets a bubble near them (preferring the `bubble_space` side, above/beside rather than below the face, off the bodies under faces, keeping the reading order, inside the panel; a narrower width is tried when the default does not fit) and a tail aimed just below the face centre (the mouth), stopping short of it; speakers without a position get the provisional tail; narration ignores positions (only nudged off faces) and goes to the reading-start corner of the `bubble_space` area. Writes `lettered/PAGE.png`, prints a line per auto placement ("check p01-1#1 (auto)"). `--debug` also writes `lettered/PAGE.debug.png`: the lettered page with slot outlines (blue), `bubble_space` areas (green), face boxes and mouth points (red), translucent.
- `imaging/export.py`: `comic export DIR EP` → slides: `out/01.jpg …` (from `lettered/`, falling back to `composed/` with a warning), grouped in `post-N/` when `per_post` is set; scroll: stitch pages top→bottom and cut into `slice_height` files. Respect `file.type`, `quality`, `max_mb` (lower JPEG quality in steps until it fits). Then `preview.write_preview(out_dir, files, preset, title)`.

## 7. Preview [D]

`preview.py`: `write_preview(out_dir: Path, files: list[Path], preset: dict, title: str) -> Path` writes `out/preview.html` — a single self-contained file (inline CSS/JS, images referenced by relative path, no network). Slides: phone-width frame at the preset aspect, prev/next buttons, arrow keys, dot indicator, page counter; when `per_post` is set, show posts one after another. Scroll: images stacked with no gaps at the preset width (scaled down on narrow screens). Light and dark friendly neutral styling.

## 8. Templates [D]

`templates/comic.yaml`, `templates/character.yaml`, `templates/script.yaml`: exactly the shapes in §2, with short English comments explaining each field. The script template contains one page with two panels and every dialogue type once, using placeholder characters `alice` and `bob`.
