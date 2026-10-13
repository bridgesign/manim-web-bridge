> **DISCLAIMER — By the Agents, for the Agents.**
> This repository was written by AI coding agents, and this README is written
> for AI coding agents that will use, debug or extend it. Humans are welcome,
> but expect agent-oriented detail. Verify behaviour with the tests below
> before trusting any statement here.

# manim-web-bridge

Write a normal [manim](https://www.manim.community/) (Community Edition) scene
in Python, get **one self-contained HTML file** that plays it in the browser:
no video, no server, no CDN, no JavaScript dependencies.

```bash
python -m manim_web_bridge examples/demo.py Demo out.html   # then open out.html
```

```python
from manim import *
from manim_web_bridge import BridgeScene

class Demo(BridgeScene):
    def construct(self):
        c = Circle()
        self.play(Create(c))
        self.next_slide()              # a click: playback stops here
        self.play(c.animate.shift(RIGHT))
```

Keys in the page: → / Space / PageDown / click = next (a first press finishes a
running step), ← / PageUp = previous, Home / End, `f` = fullscreen.
`out.html#12` opens directly at the end of step 12.

## Dependencies

| Side | Needs |
| --- | --- |
| Python | `manim>=0.19,<0.20` only (numpy, pillow come with it). LaTeX only if the scene uses `Tex`/`MathTex`, as in plain manim. |
| Browser | nothing — `player.js` is plain JavaScript on a `<canvas>` |
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
2. **Export** (`export.py`). Data (JSON) + `player.js` inlined into one HTML page.
3. **Play** (`player.js`). Drawing is a port of manim's Cairo camera
   (`Camera.set_cairo_context_path` + `display_vectorized`): 4 control points per
   cubic, new subpath where a curve doesn't start at the previous end, nonzero
   fill rule, fill then stroke, stroke width × 0.01 frame units, miter joins.
   Canvas 2D and Cairo share this model, so pictures match manim's.

### Data format (`recorder.Recorder.data()`)

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
  "steps": [{"cmds": [...], "end": [[uid, state], ...]}]   // one step per click
}
```

Commands: `set` (screen = this list), `play` (tracks
`{k, u, a, b, t, d, r}` = kind, leaf uid, start/end state, start time, duration,
rate function), `frames` (`{u: [[uid, state]...], o?: [uids]}` per frame),
`wait`. Outlines and states are deduplicated, so moving/fading a shape costs a
few bytes, not a new outline.

## Files

| File | What |
| --- | --- |
| `manim_web_bridge/scene.py` | `BridgeScene` — subclass this instead of `Scene` |
| `manim_web_bridge/recorder.py` | records `play`/`wait`/`next_slide` into steps |
| `manim_web_bridge/geometry.py` | mobject → state (points, style, images) |
| `manim_web_bridge/export.py` | writes the HTML page |
| `manim_web_bridge/player.js` | canvas player (drawing, interpolation, navigation) |
| `manim_web_bridge/__main__.py` | CLI: `python -m manim_web_bridge file.py Scene [out.html]` |
| `examples/demo.py` | one scene exercising every recording path |
| `tests/snapshot.py` | headless Chromium check: screenshots + console errors |

## Testing (do this after every change)

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
- Text and LaTeX become outlines (not selectable in the page).
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
