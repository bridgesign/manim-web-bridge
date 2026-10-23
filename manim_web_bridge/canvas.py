"""
Canvas: lay out a scene in fractions of the canvas, never in absolute numbers.

    from manim_web_bridge import Canvas

    c = Canvas()                              # the whole frame, read from manim's config
    title.move_to(c.point(0.5, 0.1))          # x, y as fractions: (0, 0) top-left, (1, 1) bottom-right
    box.set_width(c.w(0.8))                   # 80% of the canvas width
    label.next_to(box, DOWN, buff=c.h(0.02))  # 2% of the canvas height
    Text("Hello", font_size=c.font(0.04))     # capital letters 4% of the canvas height

    body = c.region(0.04, 0.15, 0.96, 0.9)    # a sub-canvas: fractions inside it from here on
    chart.move_to(body.point(0.5, 0.5))

Positions are measured from the top-left corner (like a page or a screen);
lengths are fractions of the width (w) or the height (h). A font size is the
height of a capital letter as a fraction of the canvas height, calibrated
once on the reference font (font=...; manim's Text default otherwise), so the
same fraction gives the same look on any frame size. Every helper has an
inverse (fx, fy, fw, fh, ffont) so a tool can store what it sees as
fractions too.

Nothing here is drawn or recorded; it only turns fractions into manim units
for the current frame (config.frame_width / frame_height), so it works the
same rendered natively and exported.
"""

from __future__ import annotations

import numpy as np

_CAP_PER_POINT: dict = {}        # font -> cap height (manim units) per font_size point


def cap_per_point(font: str | None = None) -> float:
    """Height of a capital "M" per font_size point, for `font` (measured once)."""
    key = font or ""
    if key not in _CAP_PER_POINT:
        from manim import Text
        probe = Text("M", font=font, font_size=100) if font else Text("M", font_size=100)
        _CAP_PER_POINT[key] = probe.height / 100
    return _CAP_PER_POINT[key]


class Canvas:
    """A rectangle of the frame (all of it by default) addressed in fractions."""

    def __init__(self, left: float | None = None, top: float | None = None,
                 right: float | None = None, bottom: float | None = None,
                 font: str | None = None, full=None):
        from manim import config
        fw, fh = config.frame_width, config.frame_height
        # edges in manim units (y up); default: the whole frame
        self.left = -fw / 2 if left is None else left
        self.right = fw / 2 if right is None else right
        self.top = fh / 2 if top is None else top
        self.bottom = -fh / 2 if bottom is None else bottom
        self.font_name = font
        self.full = full or self                # the whole canvas (fonts are relative to it)

    # -- size --------------------------------------------------------------
    @property
    def width(self) -> float:
        return self.right - self.left

    @property
    def height(self) -> float:
        return self.top - self.bottom

    # -- fractions -> manim units --------------------------------------------
    def x(self, fx: float) -> float:
        """x (manim units) at fraction fx of the width, from the left edge."""
        return self.left + fx * self.width

    def y(self, fy: float) -> float:
        """y (manim units) at fraction fy of the height, from the TOP edge."""
        return self.top - fy * self.height

    def point(self, fx: float, fy: float) -> np.ndarray:
        """The point at (fx, fy): (0, 0) top-left, (1, 1) bottom-right."""
        return np.array([self.x(fx), self.y(fy), 0.0])

    def w(self, f: float) -> float:
        """A length: fraction f of this canvas' width."""
        return f * self.width

    def h(self, f: float) -> float:
        """A length: fraction f of this canvas' height."""
        return f * self.height

    def font(self, f: float) -> float:
        """manim font_size whose capital letters are fraction f of the whole
        canvas' height (in the reference font)."""
        return f * self.full.height / cap_per_point(self.full.font_name)

    def stroke(self, f: float) -> float:
        """manim stroke_width for a line f of the canvas height thick
        (manim draws stroke_width * 0.01 units)."""
        return f * self.full.height / 0.01

    # -- manim units -> fractions (to store what is seen) ---------------------
    def fx(self, x: float) -> float:
        return (x - self.left) / self.width

    def fy(self, y: float) -> float:
        return (self.top - y) / self.height

    def fw(self, length: float) -> float:
        return length / self.width

    def fh(self, length: float) -> float:
        return length / self.height

    def ffont(self, font_size: float) -> float:
        return font_size * cap_per_point(self.full.font_name) / self.full.height

    # -- sub-canvases ----------------------------------------------------------
    def region(self, fx0: float, fy0: float, fx1: float, fy1: float) -> "Canvas":
        """The rectangle from (fx0, fy0) to (fx1, fy1) of this canvas, as a
        canvas of its own (fractions inside it; fonts stay relative to the
        whole canvas)."""
        return Canvas(left=self.x(fx0), top=self.y(fy0), right=self.x(fx1), bottom=self.y(fy1),
                      font=self.font_name, full=self.full)

    def __repr__(self):
        return (f"Canvas(x {self.left:.3f}..{self.right:.3f}, y {self.bottom:.3f}..{self.top:.3f}, "
                f"{self.width:.3f} x {self.height:.3f})")
