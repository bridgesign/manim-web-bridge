> **DISCLAIMER — By the Agents, for the Agents.**
> This repository was written by AI coding agents, and this README is written
> for AI coding agents that will use, debug or extend it. Humans are welcome,
> but expect agent-oriented detail. Verify behaviour with the tests below
> before trusting any statement here.

# manim-web-bridge

Write a normal [manim](https://www.manim.community/) (Community Edition) scene
in Python, get a web page that plays it in the browser: no video rendering,
no server, no CDN, no JavaScript dependencies.

```python
Demo.export("out/")          # folder: open out/main.html   (or export(Demo, "out/"))
Demo.export("out.html")      # or one self-contained file
```
```bash
python -m manim_web_bridge examples/demo.py Demo out/        # the same from the shell
```

**Folder** (default): `main.html` + `steps/step_NNN.js` (one plain-JS chunk per
step: its commands plus the shapes it introduces) + `media/` (images, clip
frames). The player loads a step's chunks right before showing it and
prefetches the next one, so a long deck opens instantly. Chunks are `<script>`
files and media are `<img>` files, so it works from `file://` (no `fetch`, no
server). **Single file** (`.html`): everything inlined and gzipped, for
sending one file.

```python
from manim import *
from manim_web_bridge import BridgeScene

class Demo(BridgeScene):
    def construct(self):
        c = Circle()
        self.play(Create(c))
        self.next_slide()              # a click: playback stops here
        self.play(c.animate.shift(RIGHT))
        self.next_slide(loop=True)     # the animations until the next click repeat
        self.play(Indicate(c))
        self.next_slide()
```

The same scene still renders with plain manim (`manim -pql demo.py Demo`):
outside `export()` a `BridgeScene` is an ordinary `Scene` and `next_slide()`
does nothing.

`next_slide(click=False)` ends a step without a click (the player carries
straight on), e.g. a slide's intro running into a looping animation;
`auto_next=True` (as in manim-slides) does the same for the step it opens.

Keys in the page: → / Space / PageDown / click = next (a first press finishes a
running step; on a looping step it moves straight on), ← / PageUp = previous, Home / End, `f` = fullscreen.
`out.html#12` opens directly at the end of step 12.

## Dependencies

| Side | Needs |
| --- | --- |
| Python | `manim>=0.19,<0.20` only (numpy, pillow come with it). LaTeX only if the scene uses `Tex`/`MathTex`, as in plain manim. |
| Browser | nothing — `player.js` is plain JavaScript on a `<canvas>` |
| Video (optional) | nothing for `VideoMobject(embed="webp")` (default); ffmpeg (PATH or `pip install manim-web-bridge[video]`) only for `embed="webm"` |
| Tests (optional) | `playwright` + Chromium (`python -m playwright install chromium`) |

Keep it that way: **do not add dependencies** without a strong reason.

## How it works

```
your Scene (Python)  ──record──▶  data (JSON)  ──embed──▶  out.html = data + player.js
   real manim runs here            steps / states / outlines      canvas player, Cairo-equivalent drawing
```

1. **Record** (`recorder.py`). `BridgeScene.play()` does not render. It runs
   each animation in Python (manim is the source of truth for layout, text,
   LaTeX, timing) and records the start and end state of every *leaf* (a
   VMobject with points, or an image). Each `play()` becomes one of:
   - `tween` — any `Transform` subclass with a straight path: FadeIn/Out,
     Transform, ReplacementTransform, TransformFromCopy, `.animate`,
     GrowArrow/GrowFromCenter, Indicate, ScaleInPlace, ... Start/end points are
     aligned with manim's `align_points`, so the player only interpolates numbers
     (= what manim's Transform does).
   - `create` — `Create` (port of `VMobject.pointwise_become_partial`).
   - `write` — `Write` / `DrawBorderThenFill` (outline first, then fill).
   - `frames` — everything else, sampled at 30 fps: updaters, `MoveAlongPath`,
     `Rotate`/arc paths, `Succession`, custom animations, unknown rate functions,
     two animations on the same leaf.
   Per-leaf timing for `lag_ratio`, `LaggedStart`, `AnimationGroup` follows
   manim's `get_sub_alpha` / `anims_with_timings` exactly.
2. **Export** (`export.py`). `write_site(data, folder)` writes the folder form
   (per-step chunks, each state/outline stored in the chunk of the first step that
   uses it, plus a dependency list per step); `write_html(data, file)` the single
   gzipped file. `read_html(path)` returns the data from either form.
3. **Play** (`player.js`). Drawing is a port of manim's Cairo camera
   (`Camera.set_cairo_context_path` + `display_vectorized`): 4 control points per
   cubic, new subpath where a curve doesn't start at the previous end, nonzero
   fill rule, fill then stroke, stroke width × 0.01 frame units, miter joins.
   Canvas 2D and Cairo share this model, so pictures match manim's.

### Data format (`recorder.Recorder.data()`, or `read_html(path)` on an exported page)

```jsonc
{
  "version": 1,
  "frame": [14.22, 8.0],               // manim frame size (units)
  "background": "#ffffff",
  "geoms":  [[dx0, dy0, dx1, dy1, ...]],  // outlines: control points relative to the
                                           // first one, in 1/1000 frame units (ints)
  "states": [{"g": 0, "x": -1.2, "y": 0.5,      // outline + offset
              "f": "#rrggbb", "fo": 1, "c": "#rrggbb", "co": 1, "w": 4},
             {"img": 0, "x": 0, "y": 0, "iw": 2, "ih": 1, "o": 1}],
  "images": ["data:image/png;base64,..."],
  "steps": [{"cmds": [...], "end": [[uid, state], ...],    // one step per click
             "loop": true}]                              // optional: repeat until a click
}
```

Commands: `set` (screen = this list), `play` (tracks
`{k, u, a, b, t, d, r}` = kind, leaf uid, start/end state, start time, duration,
rate function), `frames` (`{u: [[uid, state]...], o?: [uids]}` per frame),
`wait`. Outlines and states are deduplicated, so moving/fading a shape costs a
few bytes, not a new outline.

## Video clips (`VideoMobject`)

```python
from manim_web_bridge import VideoMobject
clip = VideoMobject("replay.webp", width=7.0,       # animated WebP / GIF, or a video (MP4, WebM, ...)
                    crop=(0.30, 0.10, 0.48, 0.65),  # zoom: (x, y, w, h) fractions of the frame
                    trim=(0.0, 4.0),                # cut: source seconds
                    speed=0.5, height=720, loop=True)
self.play(FadeIn(clip)); self.wait(clip.video_seconds)
```

An `ImageMobject` in Python showing the clip's first frame (layout, fades and
moves work as for any image; group it with `Group`, not `VGroup`). Rendered by
plain manim (`manim -pql ...`), it plays: an updater shows the frame for the
time the clip has been on screen. Exported, the browser plays it instead (the
recorder switches that updater off per clip).

| `embed=` | What is embedded | Plays in | Needs |
| --- | --- | --- | --- |
| `"webp"` (default) | ONE animated WebP (or the GIF/WebP source as-is), shown as an `<img>` over the canvas; the browser animates it | every viewer, incl. VS Code's built-in browser | nothing extra (Pillow; PyAV for video sources — both come with manim) |
| `"webm"` | VP9 WebM in a muted `<video>` drawn into the canvas | Chrome, Firefox, Edge — not VS Code | ffmpeg (PATH or `[video]` extra) |

Browsers only animate images they actually show, so animated clips are real
`<img>` elements kept over their image (fades = CSS opacity; a fresh
element each time the clip comes on screen restarts it). They are drawn above
the canvas. Keep source clips as animated WebP/GIF: unlike WebM/MP4 they also
open directly in VS Code. Conversions are cached in
`~/.cache/manim-web-bridge/video/`.

## Layout helper (`free_range`, `place`)

Where can a mobject go without touching others? Every mobject is one big
bounding box. `free_range` slides it along one axis and returns the
(min, max) of centre positions that keep it inside `bounds` and clear of the
obstacles by `padding` (the free interval around it, or the nearest one).
It only gives bounds; positioning stays yours, and `place` defaults to the
middle of the range. Plain manim geometry (works natively and exported).

```python
from manim_web_bridge import free_range, place, overlaps
lo, hi = free_range(diagram, [header, notes], axis="y", bounds=(-6.5, 6.5, -3.1, 2.8), padding=0.15)
place(diagram, [header, notes], axis="y", bounds=(-6.5, 6.5, -3.1, 2.8), at=0.5)   # middle (default)
assert overlaps([diagram], [header, notes], padding=0.15) == []
```

## Files

| File | What |
| --- | --- |
| `manim_web_bridge/scene.py` | `BridgeScene` — subclass this instead of `Scene` |
| `manim_web_bridge/layout.py` | `free_range` / `place` / `overlaps`: free space along an axis (bounding boxes) |
| `manim_web_bridge/recorder.py` | records `play`/`wait`/`next_slide` into steps |
| `manim_web_bridge/geometry.py` | mobject → state (points, style, images) |
| `manim_web_bridge/export.py` | writes the HTML page |
| `manim_web_bridge/media.py` | `VideoMobject`: clips as animated WebP or WebM (crop/trim/scale/speed/loop) |
| `manim_web_bridge/player.js` | canvas player (drawing, interpolation, navigation) |
| `manim_web_bridge/__main__.py` | CLI: `python -m manim_web_bridge file.py Scene [out.html]` |
| `examples/demo.py` | one scene exercising every recording path |
| `tests/snapshot.py` | headless Chromium check: screenshots + console errors |

## Testing (do this after every change)

`python tests/test_layout.py` checks the layout helper (no browser needed).

```bash
python -m manim_web_bridge examples/demo.py Demo /tmp/demo.html
python tests/snapshot.py /tmp/demo.html /tmp/shots                 # plays every step, screenshots each end, prints JS errors
python tests/snapshot.py /tmp/demo.html /tmp/shots --steps 0,2     # end states only (fast, via #n)
python tests/snapshot.py /tmp/demo.html /tmp/shots --film 1        # frames while step 1 plays
```

Look at the screenshots: an empty console is not proof that shapes render.
Delete screenshot folders afterwards.

## Known limits

- manim CE 0.19.x, Cairo renderer semantics. OpenGL-only features, 3D scenes,
  cameras (`MovingCameraScene`), gradients (only the first color is used),
  background strokes, `DashedVMobject` dash patterns via shaders — not supported
  or approximated.
- Text and LaTeX become outlines (not selectable in the page). Each glyph size is its own
  outline, so text-heavy decks are a few MB even compressed.
- Images (`ImageMobject`) are embedded as PNG and clips (`VideoMobject`) as animated WebP (or WebM); everything else is vector, drawn on the canvas. Animated clips are `<img>` overlays, so they always sit above canvas content.
- `frames` fallback stores every changed leaf per frame: long updater-driven
  animations of many shapes make big files. Prefer tweens where you can.
- Python-side `wait()` without updaters is just a pause; with updaters it is sampled.

## Extending

- **New rate function**: add its name to `RATE_FUNCS` in `recorder.py` and a
  port to `RATE` in `player.js` (copy the formula from
  `manim/utils/rate_functions.py`).
- **New native animation kind**: return a new kind from `_kind()` in
  `recorder.py`, record what the player needs in `_record_tracks`, and handle it
  in `trackLook()` in `player.js`. Port the manim code; do not invent semantics.
- Anything not handled natively falls back to `frames` automatically — correct
  but heavier. Fix size problems by adding native kinds, not by special cases.

## License

Apache-2.0 (see `LICENSE`).
