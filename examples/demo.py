"""Small scene covering each recording path: tween, create/write, lagged group, frames (updater)."""
from manim import *

from manim_web_bridge import BridgeScene


class Demo(BridgeScene):
    def construct(self):
        self.camera.background_color = WHITE
        title = Text("manim-web-bridge", font_size=56, color=BLACK).to_edge(UP)
        self.play(Write(title))                                   # write
        sq = Square(2, color=BLUE, fill_opacity=0.4).shift(LEFT * 3)
        self.play(Create(sq))                                     # create
        self.next_slide()
        dots = VGroup(*[Dot(color=RED).shift(RIGHT * i * 0.6) for i in range(6)]).shift(DOWN * 2)
        self.play(LaggedStart(*[FadeIn(d, shift=UP) for d in dots], lag_ratio=0.3))  # lagged tweens
        self.play(sq.animate.shift(RIGHT * 6).set_color(GREEN))   # .animate tween
        self.play(Transform(sq, Circle(1.2, color=ORANGE, fill_opacity=0.4).move_to(sq)))
        self.next_slide()
        eq = MathTex(r"e^{i\pi} + 1 = 0", color=BLACK).shift(UP)
        self.play(FadeIn(eq), Indicate(title))                    # Indicate: there-and-back tween
        follower = Dot(color=PURPLE).add_updater(lambda d: d.next_to(sq, UP))
        self.add(follower)
        self.play(sq.animate.shift(LEFT * 6), run_time=1.5)       # frames (updater)
