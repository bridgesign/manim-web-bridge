"""
Recorder: runs a manim scene and records what happens on screen, instead of
rendering video. The result is a list of *steps* (one per click) that the JS
player (player.js) replays on an HTML canvas.

How a `play()` is recorded
--------------------------
Python manim is the source of truth: every animation is really run here, and
we capture the start and end state of each leaf it moves. Then:

  * "tween"  - Transform-like animations (FadeIn/Out, Transform, .animate,
               GrowArrow, Indicate, ...). Start and end outlines are aligned
               with manim's own `align_points`, so the player only has to
               interpolate numbers -- exactly what manim's Transform does.
  * "create" - Create: the player draws the first `alpha` of the curves
               (port of VMobject.pointwise_become_partial).
  * "write"  - Write / DrawBorderThenFill: outline drawn first, then
               interpolated to the filled shape (port of DrawBorderThenFill).
  * anything else (updaters, MoveAlongPath, Rotate, Succession, custom
    animations, unknown rate functions): the whole play() is sampled at FPS
    frames per second and the player just shows the frames.

Per-leaf timing (lag_ratio, LaggedStart, AnimationGroup) is computed exactly
as manim does, so the browser plays the same choreography.

Commands written to a step
--------------------------
    {"op": "set",    "d": [[uid, state], ...]}        screen = exactly this list
    {"op": "play",   "T": seconds, "tr": [track, ...]}
          track = {"k": kind, "u": uid, "a": start_state, "b": end_state,
                   "t": start_time, "d": duration, "r": rate_func_name}
    {"op": "frames", "fps": n, "f": [{"u": [[uid, state], ...], "o": [uids]?}, ...]}
    {"op": "wait",   "d": seconds}

States are indices into a deduplicated state bank. Outlines are stored once in
an outline bank, relative to their first point and in 1/1000 frame units, so
moving or fading a shape only adds a tiny state {"g": outline, "x", "y", style}.
"""

import json

from manim import VMobject
from manim.animation.animation import prepare_animation
from manim.animation.composition import AnimationGroup, Succession
from manim.animation.creation import Create, DrawBorderThenFill
from manim.animation.transform import Transform
from manim.utils.rate_functions import linear

from .geometry import ImageBank, leaves, names, state_of

FPS = 30

# manim rate functions the player implements (ports in player.js)
RATE_FUNCS = {
    "linear", "smooth", "rush_into", "rush_from", "slow_into", "double_smooth",
    "there_and_back", "there_and_back_with_pause", "running_start", "wiggle",
    "lingering", "exponential_decay",
}


def _kind(anim) -> str | None:
    """How the player draws this (leaf-level) animation; None = sample frames."""
    if isinstance(anim, DrawBorderThenFill):          # includes Write
        return "write"
    if type(anim) is Create:
        return "create"
    if isinstance(anim, Transform) and not getattr(anim, "path_arc", 0):
        return "tween"
    return None


def _has_updaters(scene) -> bool:
    """Updaters that change the picture (VideoMobject's own frame updater
    only runs in native manim rendering, so it does not count)."""
    return any(not getattr(u, "bridge_native", False)
               for m in scene.get_mobject_family_members() for u in m.updaters)


class Recorder:
    def __init__(self, scene):
        self.scene = scene
        self.states, self._state_index = [], {}   # deduplicated states
        self.geoms, self._geom_index = [], {}     # deduplicated outlines
        self._memo = {}                           # fingerprint -> state index
        self.images = ImageBank()
        self.steps = []                           # finished steps
        self.cmds = []                            # commands of the current step
        self._uids, self._keep = {}, []           # id(mobject) -> uid
        self._names = []                          # uid -> readable name (for the step files)
        self._note = None                         # a note for the current step (BridgeScene.note)
        self._shown = None                        # last display list sent
        self._loop = False                        # current step repeats until a click
        self._auto = False                        # current step flows into the next one

    # ------------------------------------------------------------------
    # bookkeeping
    # ------------------------------------------------------------------
    def uid(self, m, name: str | None = None) -> int:
        key = id(m)
        if key not in self._uids:
            self._uids[key] = len(self._uids)
            self._keep.append(m)   # keep alive so id() is never reused
            self._names.append(name or type(m).__name__)
        elif name and self._names[self._uids[key]] == type(m).__name__:
            self._names[self._uids[key]] = name        # a better name, seen later
        return self._uids[key]

    def sid(self, m) -> int:
        """State index of mobject `m` as it looks right now."""
        if isinstance(m, VMobject):   # cheap fingerprint: most leaves don't change
            fp = (id(m), m.points.tobytes(), m.get_fill_rgbas().tobytes(),
                  m.get_stroke_rgbas().tobytes(), m.get_stroke_width())
        else:
            fp = (id(m), m.points.tobytes(), getattr(m, "stroke_opacity", 1.0))
        if fp in self._memo:
            return self._memo[fp]
        if getattr(m, "native_playback", False):
            m.native_playback = False   # a VideoMobject: the browser plays it, not Python

        state = state_of(m, self.images)
        if "p" in state:   # outline -> outline bank, keep only offset + style
            p = state.pop("p")
            x0, y0 = p[0], p[1]
            rel = [round((v - (x0 if i % 2 == 0 else y0)) * 1000) for i, v in enumerate(p)]
            gkey = json.dumps(rel, separators=(",", ":"))
            if gkey not in self._geom_index:
                self._geom_index[gkey] = len(self.geoms)
                self.geoms.append(rel)
            state = {"g": self._geom_index[gkey], "x": x0, "y": y0, **state}

        key = json.dumps(state, separators=(",", ":"))
        if key not in self._state_index:
            self._state_index[key] = len(self.states)
            self.states.append(state)
        self._memo[fp] = self._state_index[key]
        return self._memo[fp]

    def display(self) -> list:
        """What is on screen now: [[uid, state], ...] in drawing order."""
        out, seen = [], set()
        for top in self.scene.mobjects:
            named = names(top)
            for m in leaves(top):
                if id(m) not in seen:
                    seen.add(id(m))
                    out.append([self.uid(m, named.get(id(m))), self.sid(m)])
        return out

    def sync(self, force: bool = False):
        """Emit a 'set' if the screen changed since the last command."""
        d = self.display()
        if force or d != self._shown:
            self.cmds.append({"op": "set", "d": d})
            self._shown = d

    def note(self, text: str):
        """A note for the current step, shown in its readable step file."""
        self._note = text

    def end_step(self, loop: bool = False, auto_next: bool = False, click: bool = True):
        """Close the current step (a click). Its end state is stored so the
        player can jump straight to it.
          loop       the NEXT step repeats until the following click
                     (manim-slides' next_slide(loop=True)); it starts with a
                     forced 'set' so every repeat begins from the same screen
          auto_next  the NEXT step, once done, starts the one after it
                     without a click (manim-slides' auto_next)
          click      False: no click here -- the step being closed flows
                     straight into the next one"""
        self.sync()
        step = {"cmds": self.cmds, "end": self._shown or []}
        if self._note:
            step["note"] = self._note
        if self._loop:
            step["loop"] = True
        if self._auto or not click:
            step["auto"] = True
        self.steps.append(step)
        self.cmds = []
        self._note = None
        self._loop, self._auto = loop, auto_next
        if loop:
            self.sync(force=True)

    # ------------------------------------------------------------------
    # play
    # ------------------------------------------------------------------
    def play(self, *args, **kwargs):
        scene = self.scene
        anims = [prepare_animation(a) for a in args]
        for a in anims:
            for k, v in kwargs.items():
                setattr(a, k, v)
        self.sync()

        # same set-up as manim's Scene.play
        family = scene.get_mobject_family_members()
        for a in anims:
            if a.mobject is not None and a.mobject not in family and not a.is_introducer():
                scene.add(a.mobject)
        for a in anims:
            a._setup_scene(scene)
            a.begin()
        run_time = max(a.run_time for a in anims)

        units = self._plan(anims)
        has_updaters = _has_updaters(scene)
        if units is None or has_updaters:
            self._record_frames(anims, run_time)
        else:
            self._record_tracks(units, run_time)

        for a in anims:
            a.finish()
            a.clean_up_from_scene(scene)
        scene.update_mobjects(0)
        self.sync(force=True)   # exact end state (also resets the player's bookkeeping)

    def _plan(self, anims):
        """Flatten animations into (anim, kind, start, duration) units with
        real-time windows, or None if anything needs frame sampling."""
        units = []

        def walk(anim, t0, span):
            if isinstance(anim, Succession):
                return False
            if isinstance(anim, AnimationGroup):
                if anim.rate_func is not linear or anim.max_end_time <= 0:
                    return False
                scale = span / anim.max_end_time
                for sub, start, end in anim.anims_with_timings:
                    if not walk(sub, t0 + start * scale, (end - start) * scale):
                        return False
                return True
            kind = _kind(anim)
            if kind is None or getattr(anim.rate_func, "__name__", "") not in RATE_FUNCS:
                return False
            units.append((anim, kind, t0, span))
            return True

        for a in anims:
            if not walk(a, 0.0, a.run_time):
                return None
        # a leaf driven by two animations at once can't be split into tracks
        owned = [id(m) for a, *_ in units for m in leaves(a.mobject)]
        return units if len(owned) == len(set(owned)) else None

    def _record_tracks(self, units, run_time):
        # Start / end of every unit with the rate function switched off, so we
        # get the true target (e.g. Indicate's enlarged state); the player
        # applies the rate function itself.
        saved = {id(a): a.rate_func for a, *_ in units}
        for a, *_ in units:
            a.rate_func = linear
        starts = {}
        for a, *_ in units:
            a.interpolate(0)
            for m in leaves(a.mobject):
                starts[id(m)] = m.copy()
        self.sync(force=True)   # screen at t = 0
        ends = {}
        for a, *_ in units:
            a.interpolate(1)
            for m in leaves(a.mobject):
                ends[id(m)] = m.copy()
        for a, *_ in units:
            a.rate_func = saved[id(a)]

        tracks = []
        for anim, kind, t0, span in units:
            ms = leaves(anim.mobject)
            named = names(anim.mobject)
            outline = leaves(anim.outline) if kind == "write" else None
            n, lag = len(ms), anim.lag_ratio
            full = (n - 1) * lag + 1 if n else 1
            for i, m in enumerate(ms):
                a, b = starts[id(m)], ends[id(m)]
                if kind == "tween" and isinstance(a, VMobject) and isinstance(b, VMobject):
                    a.align_points(b)          # same point count, as manim's Transform does
                elif kind == "write":
                    a = outline[i]             # DrawBorderThenFill's outline for this leaf
                # manim's get_sub_alpha: leaf i runs during [i*lag, i*lag + 1] / full
                tracks.append({
                    "k": kind, "u": self.uid(m, named.get(id(m))), "a": self.sid(a), "b": self.sid(b),
                    "t": round(t0 + span * i * lag / full, 4), "d": round(span / full, 4),
                    "r": saved[id(anim)].__name__,
                })
        self.cmds.append({"op": "play", "T": round(run_time, 4), "tr": tracks})

    def _record_frames(self, anims, run_time):
        scene = self.scene
        n = max(1, round(run_time * FPS))
        dt = run_time / n
        frames, last = [], {u: s for u, s in self._shown or []}
        for i in range(1, n + 1):
            t = i * dt
            for a in anims:
                a.update_mobjects(dt)
                a.interpolate(min(1.0, t / a.run_time) if a.run_time > 0 else 1.0)
            scene.update_mobjects(dt)
            d = self.display()
            frame = {"u": [[u, s] for u, s in d if last.get(u) != s]}
            order = [u for u, _ in d]
            if order != list(last):
                frame["o"] = order
            frames.append(frame)
            last = {u: s for u, s in d}
        self.cmds.append({"op": "frames", "fps": n / run_time if run_time > 0 else FPS, "f": frames})
        self._shown = [[u, s] for u, s in last.items()]

    # ------------------------------------------------------------------
    # wait
    # ------------------------------------------------------------------
    def wait(self, duration: float):
        if duration <= 0:
            return
        if _has_updaters(self.scene):
            from manim import Wait
            self.play(Wait(duration))
        else:
            self.sync()
            self.cmds.append({"op": "wait", "d": round(duration, 4)})

    # ------------------------------------------------------------------
    # output
    # ------------------------------------------------------------------
    def data(self) -> dict:
        from manim import config
        if self.cmds or not self.steps:
            self.end_step()
        return {
            "version": 1,
            "frame": [config.frame_width, config.frame_height],
            "background": self.scene.camera.background_color.to_hex()[:7],
            "geoms": self.geoms,
            "states": self.states,
            "images": self.images.urls,
            "videos": self.images.videos,
            "steps": self.steps,
            "names": self._names,
        }
