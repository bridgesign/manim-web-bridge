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
    #: Optional: the scene as a list of slides, each a callable taking the scene
    #: (construct() should just call them in order). The server (serve.py)
    #: records them one at a time, so editing one slide re-records only it.
    slides: list = []

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

    def step_is_empty(self) -> bool:
        """True if nothing has been played since the last click (so ending the
        step now would add a click that does nothing)."""
        return self.recorder is None or not any(c["op"] != "set" for c in self.recorder.cmds)

    def note(self, text: str, slide_number: int | None = None):
        """Label the current step (e.g. "slide 12: Scoring"); it heads that
        step's file in the export, so a person can find it. `slide_number`:
        this step begins the slide shown as that number, so the page opens
        there from `main.html#12` (and `#12.2` is two clicks into it).
        No-op natively."""
        if self.recorder is not None:
            self.recorder.note(text, slide_number)

    @classmethod
    def export(cls, out, title: str | None = None, quiet: bool = False, readable: bool = True):
        """Record this scene and write it to `out` (a folder, or a .html file).
        Returns the page to open. See export.export."""
        from .export import export
        return export(cls, out, title=title, quiet=quiet, readable=readable)

    # -- per-slide recording (see serve.py) ---------------------------------
    def begin_slide(self, index: int, numbered_before: int) -> None:
        """Hook: about to record slides[index] on its own, after
        `numbered_before` numbered slides. Override to continue numbering."""

    def numbered_slides(self) -> int:
        """Hook: how many numbered slides this scene has made so far."""
        return 0

    def record_slide(self, index: int, numbered_before: int = 0, inline_media: bool = True) -> dict:
        """Record slides[index] alone: setup, the slide, tear_down. The data
        also says how many numbered slides it made ("numbered")."""
        self.recorder = Recorder(self, inline_media=inline_media)
        self.setup()
        self.begin_slide(index, numbered_before)
        type(self).slides[index](self)
        self.tear_down()
        data = self.recorder.data()
        data["numbered"] = self.numbered_slides() - numbered_before
        return data

    def record(self) -> dict:
        """Run the scene and return the recorded data (instead of render())."""
        self.recorder = Recorder(self)
        self.setup()
        self.construct()
        self.tear_down()
        return self.recorder.data()
