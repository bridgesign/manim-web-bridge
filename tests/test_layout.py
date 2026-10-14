"""Checks for layout.repel / connected. Run: python tests/test_layout.py"""
import numpy as np
from manim import Circle, Dot, Rectangle, Square, VGroup

from manim_web_bridge import connected, overlaps, repel


def test_repel_removes_overlaps():
    rng = np.random.default_rng(0)
    labels = [Rectangle(width=1.2, height=0.4).move_to([*rng.uniform(-1, 1, 2), 0]) for _ in range(12)]
    wall = Square(1.0).move_to([0, 0, 0])
    starts = [m.get_center().copy() for m in labels]
    assert overlaps(labels, [wall])
    repel(labels, [wall], padding=0.05)
    assert overlaps(labels, [wall]) == [], overlaps(labels, [wall])
    assert np.allclose(wall.get_center(), 0)                          # obstacles never move
    moved = max(np.linalg.norm(m.get_center() - s) for m, s in zip(labels, starts))
    assert moved < 3.0, moved                                         # stays near the hand layout


def test_apply_false_and_bounds():
    a, b = Square(1).move_to([0, 0, 0]), Square(1).move_to([0.2, 0, 0])
    shifts = repel([a, b], apply=False, bounds=(-3, 3, -2, 2))
    assert np.allclose(a.get_center(), [0, 0, 0])                     # nothing moved
    a.shift(shifts[0]), b.shift(shifts[1])
    assert overlaps([a, b]) == []


def test_leaf_hitboxes_and_components():
    # an L-shaped group: a box around it would hit the dot, its leaves do not
    ell = VGroup(Rectangle(width=2, height=0.2).move_to([0, -0.9, 0]),
                 Rectangle(width=0.2, height=2).move_to([-0.9, 0, 0]))
    dot = Dot([0.4, 0.3, 0])
    assert overlaps([ell, dot]) == [] and overlaps([ell, dot], hitbox="box")
    # touching pairs become rigid components
    node, label = Circle(0.3).move_to([0, 0, 0]), Rectangle(width=0.8, height=0.3).move_to([0.5, 0, 0])
    other = Circle(0.3).move_to([3, 0, 0])
    comps = connected([node, label, other])
    assert sorted(len(c.submobjects) for c in comps) == [1, 2]


if __name__ == "__main__":
    for name, fn in list(globals().items()):
        if name.startswith("test_"):
            fn()
            print("ok", name)
