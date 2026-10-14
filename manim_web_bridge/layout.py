"""
Overlap removal for slide layouts: push mobjects apart until their hitboxes
no longer touch, keeping each one close to where you put it.

    from manim_web_bridge import repel, connected

    labels = [edge_label_1, edge_label_2, state_name, ...]
    repel(labels, obstacles=[machine.states], padding=0.08)   # moves labels in place

    # animate it instead: get the shifts, play them
    shifts = repel(labels, obstacles=[...], apply=False)
    self.play(*[m.animate.shift(v) for m, v in zip(labels, shifts)])

    # rigid groups found automatically: things whose hitboxes already touch
    # (a node and its label, an arrow and its tag) move as one body
    repel(connected(parts), padding=0.1)

Plain manim geometry, so it works the same rendered natively and exported.

Model (inspired by d3-force's forceCollide / forceX / forceY and its
cooling "alpha", and by label-placement libraries such as labella.js; no
code taken from either):
  * body      a mobject moved rigidly. Its hitbox is the union of the
              bounding boxes of its drawn leaves ("leaves", default) or one
              box around it ("box").
  * obstacle  a mobject that never moves (diagram states, axes, ...).
  * collide   every overlapping pair of boxes (+ padding) is pushed apart
              along the axis of least overlap, pair by pair (as d3's
              forceCollide); two bodies share the push. A body on an
              obstacle takes the shortest of the four exits (left / right /
              up / down) that is clear of every obstacle.
  * anchor    a weak spring pulls each body back toward its start, so the
              result stays close to the hand-made layout.
  * bounds    optional (xmin, xmax, ymin, ymax) the bodies are kept inside.
  * alpha     the anchor spring cools each iteration (as d3's alpha), so
              collisions win in the end; the loop stops once nothing overlaps.
Fine for slide-sized problems (tens of bodies, pairwise test).
"""

import numpy as np
from manim import Group, Mobject, VMobject
from manim.mobject.types.image_mobject import AbstractImageMobject


def hitboxes(m: Mobject, mode: str = "leaves") -> np.ndarray:
    """Boxes [[xmin, ymin, xmax, ymax], ...] covering what `m` draws."""
    if mode == "box":
        parts = [m]
    else:
        parts = [x for x in m.get_family()
                 if isinstance(x, AbstractImageMobject) or (isinstance(x, VMobject) and len(x.points) > 0)]
        parts = parts or [m]
    boxes = []
    for p in parts:
        lo, hi = p.get_corner([-1, -1, 0]), p.get_corner([1, 1, 0])
        boxes.append([lo[0], lo[1], hi[0], hi[1]])
    return np.array(boxes, dtype=float)


def _push(a: np.ndarray, b: np.ndarray, pad: float, both: bool = False):
    """Smallest move of box set `a` that separates it from box set `b` (or
    None). With both=True: [shorter-axis move, other-axis move]."""
    best = None
    for p in a:
        for q in b:
            dx = min(p[2], q[2]) - max(p[0], q[0]) + pad      # overlap along x
            dy = min(p[3], q[3]) - max(p[1], q[1]) + pad      # overlap along y
            if dx <= 0 or dy <= 0:
                continue
            cx = (p[0] + p[2] - q[0] - q[2]) / 2               # a's centre relative to b's
            cy = (p[1] + p[3] - q[1] - q[3]) / 2
            vx = np.array([dx if cx >= 0 else -dx, 0.0])
            vy = np.array([0.0, dy if cy >= 0 else -dy])
            v, w = (vx, vy) if dx < dy else (vy, vx)
            if best is None or np.abs(v).sum() > np.abs(best[0]).sum():
                best = (v, w)
    if best is None:
        return None
    return list(best) if both else best[0]


def _exit(a: np.ndarray, walls: list, pad: float):
    """Shortest move that takes box set `a` clear of ALL obstacles: for every
    obstacle box it overlaps, try leaving it left, right, up or down (the
    candidate-position idea of label placers) and keep the shortest exit
    that hits no obstacle. None if `a` is already clear."""
    hit = [(p, q) for w in walls for p in a for q in w
           if min(p[2], q[2]) - max(p[0], q[0]) + pad > 0 and min(p[3], q[3]) - max(p[1], q[1]) + pad > 0]
    if not hit:
        return None
    candidates = []
    for p, q in hit:
        candidates += [np.array([q[0] - pad - p[2], 0.0]), np.array([q[2] + pad - p[0], 0.0]),
                       np.array([0.0, q[1] - pad - p[3]]), np.array([0.0, q[3] + pad - p[1]])]
    candidates.sort(key=lambda v: np.abs(v).sum())
    for v in candidates:
        moved = a + np.tile(v, 2)
        if all(_push(moved, w, pad) is None for w in walls):
            return v
    return candidates[0]                                     # boxed in: smallest step, retry next round


def repel(bodies, obstacles=(), *, padding: float = 0.05, anchor: float = 0.02,
          iterations: int = 300, bounds=None, hitbox: str = "leaves", apply: bool = True):
    """Move `bodies` (rigidly) until no two hitboxes overlap, nor any body
    an obstacle. Returns the shift of each body (3D vectors, frame units);
    with apply=False nothing is moved, so the shifts can be animated."""
    bodies = list(bodies)
    shapes = [hitboxes(b, hitbox) for b in bodies]
    walls = [hitboxes(o, hitbox) for o in obstacles]
    pos = np.zeros((len(bodies), 2))                         # offset of each body so far
    alpha = 1.0

    def inside(i, v):
        if bounds is None:
            return True
        xmin, xmax, ymin, ymax = bounds
        lo, hi = shapes[i][:, :2].min(0) + pos[i] + v, shapes[i][:, 2:].max(0) + pos[i] + v
        return lo[0] >= xmin and lo[1] >= ymin and hi[0] <= xmax and hi[1] <= ymax

    def clamp(i):
        if bounds is not None:
            xmin, xmax, ymin, ymax = bounds
            lo, hi = shapes[i][:, :2].min(0) + pos[i], shapes[i][:, 2:].max(0) + pos[i]
            pos[i] += np.maximum([xmin, ymin] - lo, 0) - np.maximum(hi - [xmax, ymax], 0)

    for _ in range(iterations):
        hits = 0
        # resolve pairs one at a time, each seeing the moves before it
        # (as d3's forceCollide does) -- converges far better than summing
        for i in range(len(bodies)):
            for j in range(i + 1, len(bodies)):              # body vs body: share the push
                options = _push(shapes[i] + np.tile(pos[i], 2), shapes[j] + np.tile(pos[j], 2), padding, both=True)
                if options is not None:
                    # shorter axis first, unless that would cross the bounds (then slide sideways)
                    v = next((o for o in options if inside(i, 0.51 * o) and inside(j, -0.51 * o)), options[0])
                    pos[i] += v * 0.51
                    pos[j] -= v * 0.51
                    hits += 1
            if walls:                                        # body vs obstacles: all of it
                v = _exit(shapes[i] + np.tile(pos[i], 2), walls, padding)
                if v is not None:
                    pos[i] += v * 1.01
                    hits += 1
            clamp(i)
        if hits == 0:
            break
        pos -= anchor * alpha * pos                          # spring back toward the start (cooling)
        alpha *= 0.97                                        # d3's alpha decay
    shifts = [np.array([x, y, 0.0]) for x, y in pos]
    if apply:
        for b, v in zip(bodies, shifts):
            b.shift(v)
    return shifts


def overlaps(bodies, obstacles=(), padding: float = 0.0, hitbox: str = "leaves") -> list:
    """Pairs (i, j) of bodies that overlap, and (i, "obstacle k") -- for checks."""
    boxes = [hitboxes(b, hitbox) for b in bodies]
    walls = [hitboxes(o, hitbox) for o in obstacles]
    out = [(i, j) for i in range(len(boxes)) for j in range(i + 1, len(boxes))
           if _push(boxes[i], boxes[j], padding) is not None]
    out += [(i, f"obstacle {k}") for i in range(len(boxes)) for k, w in enumerate(walls)
            if _push(boxes[i], w, padding) is not None]
    return out


def connected(mobjects, touch: float = 0.0, hitbox: str = "leaves") -> list:
    """Group mobjects whose hitboxes touch (within `touch`) into connected
    components (union-find). Returns one Group per component, to pass to
    repel() as rigid bodies -- e.g. a node and its label stay together."""
    mobjects = list(mobjects)
    boxes = [hitboxes(m, hitbox) for m in mobjects]
    parent = list(range(len(mobjects)))

    def root(i):
        while parent[i] != i:
            parent[i] = parent[parent[i]]
            i = parent[i]
        return i

    for i in range(len(mobjects)):
        for j in range(i + 1, len(mobjects)):
            if _push(boxes[i], boxes[j], touch) is not None:
                parent[root(i)] = root(j)
    components = {}
    for i, m in enumerate(mobjects):
        components.setdefault(root(i), []).append(m)
    return [Group(*ms) for ms in components.values()]
