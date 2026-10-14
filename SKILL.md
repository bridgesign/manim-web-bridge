---
name: manim-web-bridge
description: Turn a manim (Community Edition) scene or slide deck into one self-contained, offline HTML file that animates in the browser (canvas, no video, no JS dependencies). Use when asked to export, preview, debug or extend manim animations/slides as HTML with manim-web-bridge.
---

> **DISCLAIMER — By the Agents, for the Agents.**
> Written by AI agents for AI agents. Check claims against the code and the
> tests before relying on them.

# manim-web-bridge skill

## When to use
- A manim scene or deck must be viewable as a web page / single HTML file.
- Someone asks why an exported animation looks different from manim.
- You need to add support for a manim animation or rate function to the player.

## Build a page
1. Subclass `BridgeScene` instead of `Scene` (`from manim_web_bridge import BridgeScene`).
   Use `self.next_slide()` where the viewer should click; `self.next_slide(loop=True)` makes the
   animations up to the next `next_slide()` repeat until that click; `click=False` ends a step without
   waiting (intro flows into a loop). Everything else is plain manim, and the same scene still renders
   with `manim -pql` (BridgeScene is a plain Scene there; VideoMobject plays natively).
   VideoMobject is an ImageMobject: put it in `Group`, not `VGroup`.
   Labels colliding? `repel(labels, obstacles=[...], bounds=...)` pushes them apart (see README).
2. Export: `SceneName.export("out/")` from Python, or `python -m manim_web_bridge path/to/file.py SceneName out/` (folder: open
   `out/main.html`; steps load on demand) or `... out.html` (one self-contained file).
   The file's folder is put on `sys.path`, so decks can import sibling modules.
3. The CLI prints steps, number of states and file size. Several MB is fine.
   If it's tens of MB, look for long updater-driven animations (they fall back to frames).

## Verify (always, before reporting success)
```bash
python tests/snapshot.py out.html /tmp/shots --steps 0,1,2   # end states, fast
python tests/snapshot.py out.html /tmp/shots                 # play everything, prints JS errors
python tests/snapshot.py out.html /tmp/shots --film 3        # frames during step 3
```
Open the PNGs and actually look. Compare with what manim would render
(`manim -s file.py Scene` for a still) when in doubt. Delete `/tmp/shots` after.

## Debug checklist
| Symptom | Where to look |
| --- | --- |
| Shape missing / wrong colour | `geometry.vector_state` (style read from manim) and `paint()` in `player.js` |
| Wrong shape outline | `tracePath()` in `player.js` (port of `Camera.set_cairo_context_path`) |
| Animation jumps instead of moving | `_kind()` / `_plan()` in `recorder.py`; the play may be using `frames` or the rate function is missing from `RATE_FUNCS` |
| Morph looks wrong | `align_points` in `_record_tracks`; `lerpLook()` in `player.js` |
| Timing off with lag / LaggedStart | `_plan()` windows vs manim `get_sub_alpha` / `anims_with_timings` |
| Huge file | `frames` commands; inspect `data["steps"][i]["cmds"]` ops |
| Page blank | browser console via `tests/snapshot.py`; folder form: check `steps/step_NNN.js` exist next to `main.html`; old browsers lack `DecompressionStream` -> export with `write_html(..., compress=False)` |
| Video does not play | use `embed="webp"` (default: an animated image, works everywhere). `embed="webm"` fails in viewers without VP9, e.g. VS Code |
| Need the recorded data | `from manim_web_bridge import read_html; data = read_html('out.html')` |

## Rules for changes
- Port manim's own code (camera, rate functions, animations); do not invent semantics.
- No new dependencies (Python: manim only; browser: none).
- Rebuild `examples/demo.py` and run `tests/snapshot.py` after every change; look at the images.
- Keep README.md "Known limits" honest when you add or find limits.
