"""
BridgeScene: write a normal manim `Scene`, get an animated HTML page.

    from manim import *
    from manim_web_bridge import BridgeScene

    class Demo(BridgeScene):
        def construct(self):
            c = Circle()
            self.play(Create(c))
            self.next_slide()            # a click: the player stops here
            self.play(c.animate.shift(RIGHT))

    python -m manim_web_bridge demo.py Demo out.html

Nothing is rendered in Python: `play` and `wait` are recorded (see
recorder.py) and replayed in the browser by player.js on a <canvas>.
"""

from manim import Scene

from .recorder import Recorder


class BridgeScene(Scene):
    def __init__(self, **kwargs):
        super().__init__(**kwargs)
        self.recorder = Recorder(self)

    def play(self, *animations, **kwargs):
        self.recorder.play(*animations, **kwargs)

    def wait(self, duration: float = 1.0, *args, **kwargs):
        self.recorder.wait(duration)

    def next_slide(self, *args, **kwargs):
        """End the current step: the player pauses here until the next click.
        (Same name as manim-slides, so existing decks keep working.)"""
        self.recorder.end_step()

    def record(self) -> dict:
        """Run the scene and return the recorded data (instead of render())."""
        self.setup()
        self.construct()
        self.tear_down()
        return self.recorder.data()
