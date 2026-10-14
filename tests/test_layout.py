"""Checks for layout.free_range / place / overlaps. Run: python tests/test_layout.py"""
import numpy as np
from manim import Rectangle, Square

from manim_web_bridge import free_range, overlaps, place

BOUNDS = (-6.0, 6.0, -3.0, 3.0)


def test_free_range_between_obstacles():
    header = Rectangle(width=10, height=0.5).move_to([0, 2.5, 0])
    footer = Rectangle(width=10, height=0.5).move_to([0, -2.5, 0])
    box = Square(1.0).move_to([0, -2.2, 0])                       # overlaps the footer
    lo, hi = free_range(box, [header, footer], axis="y", bounds=BOUNDS, padding=0.1)
    # centre must keep the box 0.1 clear of both bars: (-2.25 + 0.1 + 0.5, 2.25 - 0.1 - 0.5)
    assert np.allclose([lo, hi], [-1.65, 1.65]), (lo, hi)
    place(box, [header, footer], axis="y", bounds=BOUNDS, padding=0.1)            # default: the middle
    assert np.isclose(box.get_y(), 0.0) and overlaps([box], [header, footer], 0.1) == []
    place(box, [header, footer], axis="y", bounds=BOUNDS, padding=0.1, at=0.0)    # or pinned to the bottom
    assert np.isclose(box.get_y(), -1.65)


def test_obstacles_beside_do_not_block():
    wall = Rectangle(width=1, height=6).move_to([3, 0, 0])        # not in the box's x-extent
    box = Square(1.0).move_to([0, 0, 0])
    assert np.allclose(free_range(box, [wall], axis="y", bounds=BOUNDS, padding=0.1), [-2.5, 2.5])
    lo, hi = free_range(box, [wall], axis="x", bounds=BOUNDS, padding=0.1)        # along x it is
    assert np.isclose(lo, -5.5) and np.isclose(hi, 2.5 - 0.1 - 0.5)


def test_nearest_free_interval_and_no_room():
    middle = Rectangle(width=4, height=1).move_to([0, 0, 0])
    box = Square(1.0).move_to([0, 0.2, 0])                         # inside the obstacle, closer to the top gap
    lo, hi = free_range(box, [middle], axis="y", bounds=BOUNDS, padding=0.0)
    assert np.isclose(lo, 1.0) and np.isclose(hi, 2.5)
    big = Square(7.0)
    assert free_range(big, axis="y", bounds=BOUNDS) is None        # taller than the bounds


if __name__ == "__main__":
    for name, fn in list(globals().items()):
        if name.startswith("test_"):
            fn()
            print("ok", name)
