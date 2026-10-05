# agent-comic-kit

[한국어](README.ko.md)

Make comics with an AI agent: SNS carousel comics, comic strips, manga- or webtoon-style pages. You bring a story, characters and as much or as little direction as you like. The agent runs the interview, character sheets, storyboard, page generation, panel fixes, lettering and export through the `comic` CLI in this repo.

- **Consistent characters.** Every character gets an approved character sheet first, built from a description or from your own sketches, photos or 3D screenshots. The sheet is attached to every page that shows them.
- **Direction stays yours.** Anything you specify (composition, angle, expression) is locked and followed. Anything you leave open, the agent directs.
- **Exact output formats.** Pages are generated at the target aspect ratio, split into panels, broken panels are redrawn alone, and the result is laid out on an exact canvas (Instagram 4:5, X, vertical scroll… presets are editable examples).
- **Real lettering.** Speech, shout, thought and narration bubbles are drawn by the tool, not by the image model, so there are no typos and text can be edited or translated without regenerating.

> Keywords: comic, manga, webtoon, manhwa, comic strip, AI comic generator, Claude Code skill, Codex, GPT Image.

---

## For AI agents

If a user gave you this repository URL:

1. **Check the image backend.** The default backend uses [codex-image-relay](https://github.com/Saeyeon-developer/codex-image-relay) (Windows + Codex desktop app, signed in). Install it too and set `backend.relay` in the project's `comic.yaml` (or the env var `COMIC_RELAY`) to its `imagen.ps1`. If you have your own GPT Image tool (for example inside Codex), use `backend.name: agent` instead. If neither is possible, stop and tell the user.
2. **Install as a skill**, for example for Claude Code:
   ```bash
   git clone https://github.com/Saeyeon-developer/agent-comic-kit ~/.claude/skills/agent-comic-kit
   pip install pillow pyyaml
   ```
3. **Read [`SKILL.md`](SKILL.md).** It is the complete workflow: interview → cast → storyboard → pages → review → lettering → export, with the review checklists and the rules about the user's direction.
4. **Use a strong vision model for directing and reviewing.** Every image costs the user's plan quota: generate only what the next step needs, and ask the user before retrying the same fix a third time.

---

## Requirements

- Python 3.11+ with Pillow and PyYAML
- An image backend: [codex-image-relay](https://github.com/Saeyeon-developer/codex-image-relay) (Windows, Codex desktop app), or an agent with its own GPT Image tool (`agent` backend)
- A font for your dialogue language. Noto Sans CJK / Noto Sans KR/JP/SC are found automatically if installed; otherwise set `lettering.font`.
- Optional: the [gpt-image-25](https://github.com/Saeyeon-developer/AI-video-prompt-skill/tree/main/gpt-image-25) skill for prompt craft.

## Quick start

```bash
python -m comic init my-comic --title "Crepe Day" --preset instagram-carousel
python -m comic cast new my-comic alice --name "Alice"
#   edit my-comic/comic.yaml and my-comic/cast/alice/character.yaml
python -m comic gen sheet my-comic alice
python -m comic cast approve my-comic alice
#   write my-comic/episodes/ep01/script.yaml (see templates/script.yaml)
python -m comic gen page my-comic ep01 p01
python -m comic split my-comic ep01 p01
python -m comic compose my-comic ep01 p01
python -m comic letter my-comic ep01 p01
python -m comic export my-comic ep01      # → episodes/ep01/out/ + preview.html
```

In practice you talk to your agent and it runs these commands. See [`SKILL.md`](SKILL.md) for the full workflow and command reference.

## How a page is made

```
script.yaml ─► prompt page ─► gen page ─► split ─► review each panel ─► (redraw a panel at its slot ratio ─► use)
                                                        │
                                                        ▼
                                  export ◄── letter (check, adjust) ◄── compose (exact canvas)
```

1. The page prompt is assembled from the project: exact aspect ratio, the role of every reference sheet, each character's fixed appearance text and height relation, the panel layout, one block per panel, the style, and "no text".
2. The generated page is split into panels by detecting the white gutters.
3. The agent reviews each panel against the storyboard and the sheets; a bad panel is redrawn alone at its slot's exact ratio, with the page as a style reference.
4. Panels are laid out on the exact output canvas with uniform margins, gutters and borders.
5. The agent records where each face is in every panel; bubbles are then placed automatically off the faces with tails aimed at the speaker, checked by the agent and adjusted in `lettering.yaml` (panel-relative positions) where needed.

## Output formats

Presets live in [`presets/formats/`](presets/formats). They are **examples**, not rules about what a comic, manga or webtoon "is". Copy one and change the numbers for your platform.

| Preset | Kind | Canvas | Notes |
|---|---|---|---|
| `instagram-carousel` | slides | 1080×1350 (4:5) | up to 20 slides |
| `instagram-carousel-34` | slides | 1080×1440 (3:4) | |
| `x-post` | slides | 1080×1350 (4:5) | 4 images per post, longer episodes split into posts |
| `vertical-scroll` | scroll | 800 wide | stitched and sliced |

Values checked 2026-10; platforms change their rules, so check before you rely on them.

## Status

v0.1. The full pipeline has been run end to end on a real 3-page episode (character sheets from 3D screenshots → storyboard → generation → one panel redraw → lettering → Instagram export). Example images made with original characters will be added to this README.

## Limits

- Image generation quality and consistency depend on the image model. Review is part of the workflow, not optional.
- v0.1 lays panels out in stacked rows (full width, top to bottom). Grid layouts are planned.
- The default backend is Windows-only (it relies on the Codex desktop app).

## Roadmap

- Local web editor for bubble positions, panel swaps and page order
- More image backends (Gemini / Nano Banana, NovelAI, …)
- Grid panel layouts, more presets (webtoon platforms, Threads, …)

## License

[Apache License 2.0](LICENSE)
