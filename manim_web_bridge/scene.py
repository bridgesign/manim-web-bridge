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

    Demo.export("demo/")                 # open demo/main.html
    python -m manim_web_bridge demo.py Demo demo/      # same, from the shell

When exported, nothing is rendered in Python: `play` and `wait` are
recorded (see recorder.py) and replayed in the browser by player.js on a
<canvas>. Rendered by manim itself (`manim -pql demo.py Demo`) it is a plain
`Scene`: play/wait render as usual and next_slide() does nothing.
"""

from manim import Scene

from .recorder import Recorder


class BridgeScene(Scene):
    recorder = None          # set by record(); None = plain manim rendering

    def play(self, *animations, **kwargs):
        if self.recorder is None:
            return super().play(*animations, **kwargs)
        self.recorder.play(*animations, **kwargs)

    def wait(self, duration: float = 1.0, *args, **kwargs):
        if self.recorder is None:
            return super().wait(duration, *args, **kwargs)
        self.recorder.wait(duration)

    def next_slide(self, *args, loop: bool = False, auto_next: bool = False,
                   click: bool = True, **kwargs):
        """End the current step: the player pauses here until the next click.
        loop=True: the animations from here to the next next_slide() repeat
        until that click. auto_next=True: that next step, once played, goes on
        without a click. (Same names as manim-slides, so existing decks keep
        working.) click=False: do not stop here at all -- what was played so
        far flows straight into what follows (e.g. a slide's intro into a
        looping animation). A no-op when rendered by manim itself."""
        if self.recorder is not None:
            self.recorder.end_step(loop=loop, auto_next=auto_next, click=click)

    @classmethod
    def export(cls, out, title: str | None = None, quiet: bool = False):
        """Record this scene and write it to `out` (a folder, or a .html file).
        Returns the page to open. See export.export."""
        from .export import export
        return export(cls, out, title=title, quiet=quiet)

    def record(self) -> dict:
        """Run the scene and return the recorded data (instead of render())."""
        self.recorder = Recorder(self)
        self.setup()
        self.construct()
        self.tear_down()
        return self.recorder.data()
