"""
Free space along an axis: where can a mobject go without touching others?

    from manim_web_bridge import free_range, place, overlaps

    lo, hi = free_range(diagram, obstacles=[header, footer_text], axis="y",
                        bounds=(-6.5, 6.5, -3.1, 2.8), padding=0.15)
    diagram.set_y((lo + hi) / 2)                 # positioning stays yours ...
    place(diagram, [header, footer_text], axis="y", bounds=..., at=0.5)   # ... or the default: the middle

Every mobject is ONE big bounding box (its hitbox). free_range slides the
mobject along one axis, keeping the other coordinate, and returns the
interval (min, max) of centre positions where its box stays inside
`bounds` and clear of every obstacle box by `padding` -- the free interval
around where it is now (or the nearest one). It only gives bounds; it does
not lay anything out. Plain manim geometry, so it works the same rendered
natively and exported.
"""

import numpy as np
from manim import Mobject

AXES = {"x": 0, "y": 1}


def hitbox(m: Mobject) -> np.ndarray:
    """[xmin, ymin, xmax, ymax] of the mobject's bounding box."""
    lo, hi = m.get_corner([-1, -1, 0]), m.get_corner([1, 1, 0])
    return np.array([lo[0], lo[1], hi[0], hi[1]], dtype=float)


def _touch(a: np.ndarray, b: np.ndarray, pad: float) -> bool:
    return (min(a[2], b[2]) - max(a[0], b[0]) + pad > 1e-9 and
            min(a[3], b[3]) - max(a[1], b[1]) + pad > 1e-9)


def overlaps(mobjects, obstacles=(), padding: float = 0.0) -> list:
    """Pairs (i, j) of mobjects whose boxes come within `padding`, and
    (i, "obstacle k") -- for checks."""
    boxes, walls = [hitbox(m) for m in mobjects], [hitbox(o) for o in obstacles]
    out = [(i, j) for i in range(len(boxes)) for j in range(i + 1, len(boxes)) if _touch(boxes[i], boxes[j], padding)]
    out += [(i, f"obstacle {k}") for i, b in enumerate(boxes) for k, w in enumerate(walls) if _touch(b, w, padding)]
    return out


def free_range(m: Mobject, obstacles=(), axis: str = "y", bounds=None, padding: float = 0.1):
    """(min, max) centre position of `m` along `axis` ("x" or "y") such that
    its box stays inside `bounds` = (xmin, xmax, ymin, ymax) and clear of
    every obstacle by `padding`. The interval around the current position,
    or the nearest free one. None if there is no room at all."""
    k = AXES[axis]
    o = 1 - k                                           # the other axis, kept fixed
    box = hitbox(m)
    half = (box[k + 2] - box[k]) / 2
    centre = (box[k + 2] + box[k]) / 2
    if bounds is not None:
        lo, hi = (bounds[0], bounds[1]) if k == 0 else (bounds[2], bounds[3])
        lo, hi = lo + half, hi - half
    else:
        lo, hi = -np.inf, np.inf
    if lo > hi:
        return None
    # obstacles in the way: those overlapping m's extent on the other axis
    blocked = []
    for w in (hitbox(x) for x in obstacles):
        if min(box[o + 2], w[o + 2]) - max(box[o], w[o]) + padding > 1e-9:
            blocked.append((w[k] - padding - half, w[k + 2] + padding + half))
    free, start = [], lo                                # complement of the blocked intervals
    for a, b in sorted(blocked):
        if a > start:
            free.append((start, min(a, hi)))
        start = max(start, b)
        if start >= hi:
            break
    if start < hi:
        free.append((start, hi))
    free = [(a, b) for a, b in free if b >= a]
    if not free:
        return None
    return min(free, key=lambda ab: 0.0 if ab[0] <= centre <= ab[1] else min(abs(centre - ab[0]), abs(centre - ab[1])))


def place(m: Mobject, obstacles=(), axis: str = "y", bounds=None, padding: float = 0.1, at: float = 0.5):
    """Move `m` along `axis` to `at` (0 = min, 1 = max, default the middle)
    of its free range. Returns the range (or None, leaving `m` where it is)."""
    span = free_range(m, obstacles, axis, bounds, padding)
    if span is None:
        return None
    lo, hi = span
    target = lo + at * (hi - lo) if np.isfinite(lo) and np.isfinite(hi) else (lo if np.isfinite(lo) else hi)
    if axis == "y":
        m.set_y(target)
    else:
        m.set_x(target)
    return span
