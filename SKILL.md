---
name: agent-comic-kit
description: Make comics with AI image generation — SNS carousel comics, comic strips, manga- or webtoon-style pages — from a story idea or a full storyboard, with consistent characters. Covers the start-up interview, character sheets (from text or the user's own images), storyboarding and direction, page generation, panel fixes, lettering (speech, shout, thought, narration) and export. Use when the user wants to create or continue a comic, design comic characters, or turn a story into panels.
---

# agent-comic-kit

You direct a comic with the user, and the `comic` CLI (in this folder) handles files, prompts, image generation, panel splitting, layout, lettering and export. Run it as `python -m comic …` from this folder, or `comic …` after `pip install -e .`.

**Recommended model.** Directing (storyboards, panel composition, prompt writing) and reviewing (reading generated images for character consistency and intent) need strong vision and a feel for comic direction. Use a top-tier model for those steps, for example gpt-6-astra, Claude Opus 5.5, or the latest Claude Fable model. The image call itself needs nothing special.

**Image generation.** Default backend `codex-relay` uses [codex-image-relay](https://github.com/Saeyeon-developer/codex-image-relay) (Windows + Codex desktop app, ChatGPT login). If you have your own GPT Image tool (for example inside Codex), set `backend.name: agent`: the CLI then tells you what to generate (exit code 3) and you bring the file back with `comic import`. For prompt craft, the [gpt-image-25 skill](https://github.com/Saeyeon-developer/AI-video-prompt-skill/tree/main/gpt-image-25) is a good companion.

Every generated image uses the user's plan quota. Generate only what the next step needs.

## 1. Start: interview, then `comic init`

Do not assume a format, reading direction or art style. Ask about anything the user has not already told you, offering a recommendation when they are unsure:

| Ask | Goes to `comic.yaml` |
|---|---|
| Where will it be posted? Slides (carousel) or vertical scroll? Size? (`comic presets` lists examples) | `format.preset`, `format.overrides` |
| Reading order of panels and bubbles: left→right, right→left, or top→bottom? | `reading_direction` |
| Art style, ideally with reference images | `style.description`, `style.refs` |
| Colour: full colour, black and white, B/W with tones, accent colours | `style.color` |
| Dialogue language, lettering feel | `language`, `lettering.font` |
| Length: pages per episode/post, panels per page | your storyboard |
| How much they want to direct vs. leave to you | how you treat `locked` fields |

Write the style as visible traits ("Japanese anime fan-art style, full colour, clean line art, cel shading with soft gradients"), never as a genre word like "manga" or "webtoon": genre words push the image model toward formats the user did not ask for (screentone B/W, tall scroll layouts).

```
comic init my-comic --title "…" --preset instagram-carousel
```

Then edit `my-comic/comic.yaml` with the answers. `comic doctor my-comic` checks fonts and the image backend.

## 2. Cast: character sheets

The sheet is the identity anchor for every page. **A character without an approved sheet must not appear in a page** — the CLI enforces this.

1. `comic cast new my-comic <id> --name "<Name>"`.
2. Fill `cast/<id>/character.yaml`: `appearance` (short, concrete, visible traits: hair, eyes, build, outfit, accessories — this text is pasted into every prompt), `personality`, `height` (relative to others), `expressions` (four that fit the personality). Put the user's source images (sketches, photos, 3D screenshots) in `cast/<id>/inputs/` and list them in `inputs`.
3. `comic prompt sheet my-comic <id>` → read and improve the prompt file → `comic gen sheet my-comic <id>`.
4. Show the sheet to the user. Apply feedback one issue at a time (edit the prompt, regenerate).
5. When the user approves: `comic cast approve my-comic <id>`.

## 3. Storyboard: `episodes/<ep>/script.yaml`

Start from `templates/script.yaml`. One page = one slide (or one scroll segment). For each panel set `shot`, `characters`, `action`, `background`, `bubble_space`, `weight` (relative height), and `dialogue` (`narration`, `speech`, `shout`, `thought`).

**Who decides what.** If the user specified something (composition, angle, expression, layout), copy it faithfully and add the field name to `locked`. Never change a locked field; suggest alternatives in chat instead. Everything the user left open is yours to direct.

When you direct:
- Each page should land one beat; end slides on a small hook or punchline so the reader swipes on.
- Vary shot sizes (establishing → medium → close-up for the emotional beat) and avoid two identical framings in a row.
- Put the first speaker where the reading order starts; keep `bubble_space` on the side where the bubble will be read first and away from faces.
- 1–3 panels per page for phone-sized slides; give the key panel the largest `weight`.
- State the height relation of characters who share a panel in their `height` fields.
- Refer to characters in `action` text by exactly their `name` from character.yaml. The prompt introduces each reference sheet by that name; a different spelling (another language, a nickname) can make the image model treat them as different people.
- Write `action` and `background` in English even when the dialogue is in another language; only dialogue goes into the bubbles.

Show the storyboard to the user and get a go-ahead before generating pages.

## 4. Pages: generate → split → review → fix

For each page:

```
comic prompt page my-comic ep01 p01      # assemble; read the prompt file and refine it
comic gen page my-comic ep01 p01         # → pages/p01.vN.png (at the preset's exact aspect ratio)
comic split my-comic ep01 p01            # → panels/p01-<id>.vN.png
```

If `split` finds a different number of panels than the script, look at the page before regenerating anything. Common causes: something (hair, a hand, an effect) breaks through a panel border into the gutter, leaving only a thin white line — retry with `--min-gutter 4`; warm or tinted lighting makes the gutter off-white — retry with `--white 220`. If neither works, pass the panel boxes yourself with `--boxes`, or regenerate the page.

**Review every panel** against the script and the sheets. Look at the image; do not assume. Check:
- each character's face, hair, outfit and accessories match the sheet; nobody swapped features
- height relation, who is where, gaze direction
- the action and expression the script asks for
- the `bubble_space` area is actually free
- hands, props, anatomy errors that would distract a reader

Partial framing (a cut-off accessory, a head cropped by the panel edge) is normal comic direction — do not "fix" it unless it hides something the script needs.

To fix one panel:

```
comic prompt panel my-comic ep01 p01 2   # prompt at that slot's exact aspect; edit it to name the one problem to fix
comic gen panel my-comic ep01 p01 2      # → panels/p01-2.vN.png (not used yet)
comic use my-comic ep01 p01 2 <that file>   # after you have looked at it and it is better
```

Fix one problem per attempt. If the same problem survives two attempts, stop and ask the user (accept, rewrite the panel, or change the storyboard) instead of regenerating in a loop. If many panels on a page are wrong, regenerate the page.

## 5. Layout and lettering

```
comic compose my-comic ep01 p01          # panels onto the exact output canvas; prints crop % per slot
comic letter my-comic ep01 p01           # bubbles from episodes/ep01/lettering.yaml (auto-placed if missing)
```

Before lettering, look at each composed panel and record where every face is in `script.yaml`, as fractions of the panel:

```yaml
      positions: {alice: [0.58, 0.16], bob: [0.33, 0.33]}   # face centres; include extras (e.g. clerk) whose faces must stay visible
```

With positions, automatic placement keeps bubbles off faces and aims tails at the speaker's mouth. Without them, tails are only placeholders. `comic letter … --debug` writes `lettered/PAGE.debug.png` showing the face boxes and bubble areas the tool assumed.

`compose` crops each panel to its slot. A high crop % means a panel was generated at the wrong aspect; set the panel's `focus` (0 = keep top … 1 = keep bottom) or regenerate it.

After `letter`, **look at `lettered/p01.png`**. For every bubble the tool placed automatically (`auto: true` in `lettering.yaml`), and any bubble that covers a face, hand or key prop, edit its `at` / `width` / `tail` (fractions of the panel) and run `letter` again. Remove `auto: true` from placements you have checked. Usually one adjustment round is enough. Bubbles should read in `reading_direction` order, and tails should point at the speaker's mouth.

## 6. Export and hand-off

```
comic export my-comic ep01               # out/01.jpg … in the preset's format + out/preview.html
comic status my-comic ep01               # checklist of what is done
```

Send the user `out/preview.html` (or the images) and list anything you could not resolve.

## Command reference

| Command | Does |
|---|---|
| `comic init DIR [--title T] [--preset P]` | new project |
| `comic presets` | list output presets (examples; users can add their own in `presets/formats/`) |
| `comic status DIR [EP]` / `comic doctor [DIR]` | progress checklist / environment check |
| `comic cast new DIR ID --name N` / `comic cast approve DIR ID` | create / approve a character |
| `comic prompt sheet DIR ID` / `comic gen sheet DIR ID` | character sheet |
| `comic prompt page DIR EP PAGE` / `comic gen page DIR EP PAGE [--fresh]` | page |
| `comic prompt panel DIR EP PAGE PANEL` / `comic gen panel …` | single-panel redraw |
| `comic import DIR EP (page PAGE \| panel PAGE PANEL \| sheet ID) IMAGE` | bring in an image you generated yourself (agent backend) |
| `comic split DIR EP PAGE [--boxes …]` / `comic use DIR EP PAGE PANEL IMAGE` | split a page / choose a panel version |
| `comic compose DIR EP [PAGE…]` / `comic letter DIR EP [PAGE…]` / `comic export DIR EP` | layout / lettering / final files |

Exit codes: `0` ok, `1` error (read the message; it says what to do), `3` waiting for you to generate an image (agent backend).
