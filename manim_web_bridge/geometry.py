"""
Mobject -> plain-data "state" that the JS player draws.

Only *leaves* are sent: the family members that actually draw something
(a VMobject with points, or an image). Groups are a Python-side concept.

Points are kept in manim's own format -- 4 control points per cubic Bezier
curve (a0 h0 h1 a1), a new subpath wherever a curve does not start where the
previous one ended -- and the player draws them exactly as manim's Cairo
camera does (Camera.set_cairo_context_path), so no conversion can drift.

    vector  {"p": [x0, y0, x1, y1, ...],   # control points (frame units)
             "f": "#rrggbb", "fo": 1.0,    # fill color / opacity
             "c": "#rrggbb", "co": 1.0,    # stroke color / opacity
             "w": 4.0}                     # stroke width (manim units)
    image   {"img": i, "x": cx, "y": cy, "iw": width, "ih": height, "o": opacity}
    video   {"vid": i, "x": cx, "y": cy, "iw": width, "ih": height, "o": opacity}
            (videos: [{"anim": WebP/GIF data URL, "loop"}] or [{"src": WebM data URL, "loop"}])

The recorder then moves "p" into a shared outline bank (see recorder.py).
"""

import base64
import hashlib
import io
from pathlib import Path

import numpy as np
from manim import VMobject
from manim.mobject.types.image_mobject import AbstractImageMobject
from PIL import Image

DECIMALS = 3  # 0.001 frame units = 0.14 px on a 1080p screen


def leaves(mobject):
    """Family members that draw something, in drawing order."""
    return [
        m for m in mobject.get_family()
        if isinstance(m, AbstractImageMobject) or (isinstance(m, VMobject) and len(m.points) > 0)
    ]


def _hex(color) -> str:
    return color.to_hex()[:7] if color is not None else "#000000"


def vector_state(m: VMobject) -> dict:
    return {
        "p": np.round(m.points[:, :2], DECIMALS).ravel().tolist(),
        "f": _hex(m.get_fill_color()), "fo": round(float(m.get_fill_opacity()), 3),
        "c": _hex(m.get_stroke_color()), "co": round(float(m.get_stroke_opacity()), 3),
        "w": round(float(m.get_stroke_width()), 3),
    }


class ImageBank:
    """Images are stored once (as PNG data URLs) and referenced by index;
    videos likewise (as MP4 data URLs, in .videos)."""

    def __init__(self):
        self.urls, self._index = [], {}
        self.videos, self._video_index = [], {}

    def add_video(self, path: str, loop: bool = False, anim: bool = False) -> int:
        """A clip: an animated image (`anim`, WebP or GIF; the browser animates
        it) or a WebM for a <video>."""
        key = (path, loop, anim)
        if key not in self._video_index:
            self._video_index[key] = len(self.videos)
            ext = Path(path).suffix.lower().lstrip(".")
            mime = {"webp": "image/webp", "gif": "image/gif", "webm": "video/webm"}.get(ext, "video/webm")
            url = f"data:{mime};base64," + base64.b64encode(Path(path).read_bytes()).decode()
            self.videos.append({"anim": url, "loop": bool(loop)} if anim else {"src": url, "loop": bool(loop)})
        return self._video_index[key]

    def add(self, pixels: np.ndarray) -> int:
        key = hashlib.sha1(pixels.tobytes()).hexdigest()
        if key not in self._index:
            buf = io.BytesIO()
            Image.fromarray(pixels).save(buf, format="PNG")
            self._index[key] = len(self.urls)
            self.urls.append("data:image/png;base64," + base64.b64encode(buf.getvalue()).decode())
        return self._index[key]


def image_state(m: AbstractImageMobject, images: ImageBank) -> dict:
    # manim bakes opacity into the pixels' alpha channel (so a fade would
    # produce a new image every frame). Store the image once at full opacity
    # and send the opacity, which manim also tracks in stroke_opacity.
    pixels = m.pixel_array
    if getattr(m, "orig_alpha_pixel_array", None) is not None:
        pixels = pixels.copy()
        pixels[:, :, 3] = m.orig_alpha_pixel_array
    cx, cy, _ = m.get_center()
    return {
        "img": images.add(pixels),
        "x": round(float(cx), DECIMALS), "y": round(float(cy), DECIMALS),
        "iw": round(float(m.width), DECIMALS), "ih": round(float(m.height), DECIMALS),
        "o": round(float(m.stroke_opacity), 3),
    }


def video_state(m, images: ImageBank) -> dict:
    cx, cy, _ = m.get_center()
    return {
        "vid": images.add_video(m.video_path, getattr(m, "video_loop", False), getattr(m, "video_anim", False)),
        "x": round(float(cx), DECIMALS), "y": round(float(cy), DECIMALS),
        "iw": round(float(m.width), DECIMALS), "ih": round(float(m.height), DECIMALS),
        "o": round(float(m.get_fill_opacity()), 3),
    }


def state_of(m, images: ImageBank) -> dict:
    if hasattr(m, "video_path"):
        return video_state(m, images)
    if isinstance(m, AbstractImageMobject):
        return image_state(m, images)
    return vector_state(m)
