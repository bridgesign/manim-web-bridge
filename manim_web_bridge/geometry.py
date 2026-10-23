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
            (videos: [{"name", "kind": "image" | "video", "loop", "url": data URL or None}])

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


def _short(text: str, n: int = 40) -> str:
    text = " ".join(str(text).split())
    return text if len(text) <= n else text[: n - 1] + "…"


def _tex_text(tex: str) -> str:
    """LaTeX -> roughly its visible text, for names: the part after an
    \\item[...] label (bullet lists), formatting commands dropped."""
    import re
    i = tex.find("\\item[")
    if i >= 0:                                          # skip the item's [label], brackets balanced
        depth, j = 0, i + 5
        while j < len(tex):
            depth += {"[": 1, "]": -1}.get(tex[j], 0)
            j += 1
            if depth == 0:
                break
        tex = tex[j:]
    tex = re.sub(r"\\(begin|end)\{[^}]*\}(\{[^}]*\})?", " ", tex)
    tex = re.sub(r"\\setlength\{[^}]*\}\{[^}]*\}", " ", tex)
    for _ in range(3):                                  # \textbf{x}, \emph{x}, \text{x} -> x
        tex = re.sub(r"\\[a-zA-Z]+\{([^{}]*)\}", r"\1", tex)
    words = ("alpha|beta|gamma|delta|epsilon|lambda|mu|pi|phi|psi|sigma|theta|omega|Phi|Sigma|Delta|"
             "tanh|sinh|cosh|sin|cos|log|exp|max|min|sum|top")
    tex = re.sub(r"\\(" + words + r")\b", r"\1", tex)      # \phi -> phi, \max -> max
    tex = re.sub(r"\\[a-zA-Z]+", " ", tex)                    # other commands: dropped
    tex = re.sub(r"\\[,;:! ]", " ", tex)                      # spacing: \, \; \: \!
    tex = re.sub(r"\s+", " ", tex)
    return tex.replace("$", "").replace("{", "").replace("}", "")


def _own_name(m) -> str | None:
    """A readable name for objects people recognise: a piece of text, a clip."""
    from manim import MarkupText, Paragraph, Text
    from manim.mobject.text.tex_mobject import SingleStringMathTex
    if hasattr(m, "video_name"):
        return f"VideoMobject {m.video_name}"
    if isinstance(m, (Text, MarkupText, Paragraph)) and getattr(m, "original_text", None):
        return f'{type(m).__name__} "{_short(m.original_text)}"'
    if isinstance(m, SingleStringMathTex):
        kind = "Tex" if type(m).__name__ in ("Tex", "SingleStringMathTex") and "\\item" in m.tex_string else type(m).__name__
        return f'{kind} "{_short(_tex_text(m.tex_string))}"'
    return None


def names(mobject, inherited: str | None = None, out: dict | None = None) -> dict:
    """id(leaf) -> a readable name for each drawn leaf of `mobject`: the text
    or clip it belongs to, else its own type (an arrow's tip is "Arrow")."""
    out = {} if out is None else out
    own = _own_name(mobject)
    draws = isinstance(mobject, AbstractImageMobject) or (isinstance(mobject, VMobject) and len(mobject.points) > 0)
    name = own or inherited or (type(mobject).__name__ if draws else None)
    if draws:
        out[id(mobject)] = name
    for sub in mobject.submobjects:
        names(sub, name, out)
    return out


def sources(mobject, inherited=None, out: dict | None = None) -> dict:
    """id(leaf) -> where it was made ([[file, line], ...], see serve.track_sources):
    a piece of text or a clip speaks for all its parts (manim reuses glyphs
    between texts, so a glyph's own record may point elsewhere)."""
    out = {} if out is None else out
    own = mobject.__dict__.get("_bridge_src")
    src = inherited or (own if _own_name(mobject) else None)
    draws = isinstance(mobject, AbstractImageMobject) or (isinstance(mobject, VMobject) and len(mobject.points) > 0)
    if draws:
        out[id(mobject)] = src or own
    for sub in mobject.submobjects:
        sources(sub, src, out)
    return out


def vector_state(m: VMobject) -> dict:
    return {
        "p": np.round(m.points[:, :2], DECIMALS).ravel().tolist(),
        "f": _hex(m.get_fill_color()), "fo": round(float(m.get_fill_opacity()), 3),
        "c": _hex(m.get_stroke_color()), "co": round(float(m.get_stroke_opacity()), 3),
        "w": round(float(m.get_stroke_width()), 3),
    }


class ImageBank:
    """Images are stored once (as PNG data URLs) and referenced by index;
    clips likewise (in .videos, see add_video)."""

    def __init__(self, inline_media: bool = True):
        self.inline_media = inline_media
        self.urls, self._index = [], {}
        self.videos, self._video_index = [], {}

    MIME = {"webp": "image/webp", "gif": "image/gif", "png": "image/png", "apng": "image/apng",
            "jpg": "image/jpeg", "jpeg": "image/jpeg", "webm": "video/webm", "mp4": "video/mp4",
            "mov": "video/quicktime", "m4v": "video/mp4", "ogv": "video/ogg"}

    def add_video(self, m) -> int:
        """A VideoMobject's clip, stored once:
        {"name": file name, "kind": "image" | "video", "loop": bool,
         "url": data URL of the file, or None if it does not exist (yet)}."""
        key = (m.video_path or "missing:" + m.video_name, m.video_loop)
        if key not in self._video_index:
            self._video_index[key] = len(self.videos)
            url = None
            if m.video_path and not self.inline_media:
                url = "file:" + str(Path(m.video_path).resolve())   # served from where it is
            elif m.video_path:
                ext = Path(m.video_path).suffix.lower().lstrip(".")
                mime = self.MIME.get(ext, "application/octet-stream")
                url = f"data:{mime};base64," + base64.b64encode(Path(m.video_path).read_bytes()).decode()
            self.videos.append({"name": m.video_name, "kind": m.video_kind, "loop": bool(m.video_loop), "url": url})
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
        "vid": images.add_video(m),
        "x": round(float(cx), DECIMALS), "y": round(float(cy), DECIMALS),
        "iw": round(float(m.width), DECIMALS), "ih": round(float(m.height), DECIMALS),
        "o": round(float(m.stroke_opacity), 3),      # an ImageMobject: like image_state
    }


def state_of(m, images: ImageBank) -> dict:
    if hasattr(m, "video_path"):
        return video_state(m, images)
    if isinstance(m, AbstractImageMobject):
        return image_state(m, images)
    return vector_state(m)
