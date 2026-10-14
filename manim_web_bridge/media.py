"""
VideoMobject: a video placed in the scene like an image.

    from manim_web_bridge import VideoMobject

    clip = VideoMobject(
        "replay.webm",
        width=7.0,                       # size on the slide (frame units)
        crop=(0.30, 0.10, 0.48, 0.65),   # zoom: (x, y, w, h) as fractions of the source frame
        trim=(0.0, 4.0),                 # cut: keep source seconds 0-4
        speed=0.5,                       # play at half speed
        height=720,                      # output resolution (pixels, default 720p)
        loop=True,                       # repeat while on screen
    )
    self.play(FadeIn(clip))              # fades, moves and scaling work like any shape

It is an ImageMobject (showing the clip's first frame), so layout, fades and
moves behave like any image. Exported, the recorder embeds the clip once and
the player shows it at the image's place; it starts from the beginning when
it comes on screen. Rendered by manim itself, an updater swaps in the frame
for the time the clip has been on screen, so the same scene also works as a
plain manim video.

Two ways to embed (`embed=`):
  "webp" (default)  ONE animated WebP that the browser animates itself
                    (shown as an <img> over the canvas at the clip's place;
                    fades are CSS opacity). An animated WebP / GIF source with
                    no options is embedded as-is; otherwise it is re-encoded
                    once (Pillow) with the crop / trim / speed / size applied.
                    Works in every viewer, including VS Code's built-in
                    browser (images, not video codecs). Video sources (MP4,
                    WebM, ...) are read with PyAV, which manim already uses.
  "webm"            a VP9 WebM in a muted <video> (smaller for long clips,
                    but VS Code cannot play it). Needs ffmpeg (PATH or the
                    optional `imageio-ffmpeg`).
Both are cached by source content + options, so rebuilding does not
re-encode.
"""

import hashlib
import json
import re
import shutil
import subprocess
from pathlib import Path

import numpy as np
from manim import ImageMobject

CACHE_DIR = Path.home() / ".cache" / "manim-web-bridge" / "video"


def find_ffmpeg() -> str:
    exe = shutil.which("ffmpeg")
    if exe:
        return exe
    try:
        import imageio_ffmpeg
        return imageio_ffmpeg.get_ffmpeg_exe()
    except ImportError:
        raise RuntimeError("VideoMobject needs ffmpeg to convert this clip: install ffmpeg "
                           "(on PATH) or `pip install imageio-ffmpeg`, or pass a .webm "
                           "source with no crop/trim/speed/fps/height options") from None


def probe(path: Path) -> dict:
    """Width, height and duration of a video (parsed from `ffmpeg -i`)."""
    out = subprocess.run([find_ffmpeg(), "-hide_banner", "-i", str(path)],
                         capture_output=True, text=True).stderr
    size = re.search(r"Video:.*?(\d{2,5})x(\d{2,5})", out)
    dur = re.search(r"Duration: (\d+):(\d+):([\d.]+)", out)
    if not size:
        raise RuntimeError(f"could not read video size of {path}:\n{out[-500:]}")
    seconds = int(dur[1]) * 3600 + int(dur[2]) * 60 + float(dur[3]) if dur else 0.0
    return {"w": int(size[1]), "h": int(size[2]), "seconds": seconds}


def encode_webm(source: Path, crop=None, trim=None, height: int = 720, fps=None,
                speed: float = 1.0, crf: int = 34) -> tuple[Path, dict]:
    """Re-encode `source` as VP9 WebM with the given options (cached).
    Returns (path, {"w", "h", "seconds"}) of the result."""
    source = Path(source).resolve()
    options = {"crop": crop, "trim": trim, "height": height, "fps": fps, "speed": speed, "crf": crf}
    key = hashlib.sha1(source.read_bytes() + json.dumps(options, sort_keys=True).encode()).hexdigest()[:20]
    out = CACHE_DIR / f"{key}.webm"
    meta_path = out.with_suffix(".json")
    if out.exists() and meta_path.exists():
        return out, json.loads(meta_path.read_text())

    filters = []
    if crop:
        x, y, w, h = crop
        filters.append(f"crop=iw*{w}:ih*{h}:iw*{x}:ih*{y}")
    if speed != 1.0:
        filters.append(f"setpts=PTS/{speed}")
    if height:
        filters.append(f"scale=-2:{int(height)}:flags=lanczos")   # keep aspect, even width
    if fps:
        filters.append(f"fps={fps}")
    cmd = [find_ffmpeg(), "-y", "-loglevel", "error"]
    if trim:
        cmd += ["-ss", str(trim[0]), "-to", str(trim[1])]
    cmd += ["-i", str(source)]
    if filters:
        cmd += ["-vf", ",".join(filters)]
    cmd += ["-an", "-c:v", "libvpx-vp9", "-b:v", "0", "-crf", str(crf), "-row-mt", "1",
            "-deadline", "good", "-cpu-used", "4", "-pix_fmt", "yuv420p", str(out)]
    CACHE_DIR.mkdir(parents=True, exist_ok=True)
    subprocess.run(cmd, check=True)
    meta = probe(out)
    meta_path.write_text(json.dumps(meta))
    return out, meta


def _source_frames(source: Path):
    """(time in seconds, PIL image) for every frame of a video (PyAV) or an
    animated image -- GIF, animated WebP, APNG -- (Pillow, using each
    frame's own duration)."""
    from PIL import Image, ImageSequence
    if source.suffix.lower() in (".gif", ".webp", ".png", ".apng"):
        with Image.open(source) as im:
            t = 0.0
            for frame in ImageSequence.Iterator(im):
                yield t, frame.convert("RGB")
                t += (frame.info.get("duration") or 100) / 1000
            yield t, None          # end marker: the last frame lasts its full duration
        return
    import av
    with av.open(str(source)) as container:
        for frame in container.decode(video=0):
            yield float(frame.time or 0.0), frame.to_image()


def encode_animated_webp(source: Path, crop=None, trim=None, height=720, speed: float = 1.0,
                         loop: bool = True, quality: int = 80) -> tuple[Path, dict]:
    """Re-encode `source` (video or animated image) as ONE animated WebP with
    crop / trim / scale / speed applied; each frame keeps its own duration.
    Cached. Returns (path, {"w", "h", "seconds"})."""
    from PIL import Image

    source = Path(source).resolve()
    options = {"crop": crop, "trim": trim, "height": height, "speed": speed, "loop": loop, "q": quality}
    key = hashlib.sha1(source.read_bytes() + json.dumps(options, sort_keys=True).encode()).hexdigest()[:20]
    out = CACHE_DIR / f"{key}.webp"
    meta_path = out.with_suffix(".json")
    if out.exists() and meta_path.exists():
        return out, json.loads(meta_path.read_text())

    t_start, t_end = trim if trim else (0.0, float("inf"))
    times, images = [], []
    for t, img in _source_frames(source):
        if img is None or t > t_end:          # end of the clip (or of the trim)
            times.append(min(t, t_end))
            break
        if t < t_start:
            continue
        if crop:
            x, y, w, h = crop
            W, H = img.size
            img = img.crop((round(x * W), round(y * H), round((x + w) * W), round((y + h) * H)))
        if height and img.height != height:
            img = img.resize((round(img.width * height / img.height), height), Image.LANCZOS)
        times.append(t)
        images.append(img)
    if len(times) == len(images):              # video ran out without an end marker
        times.append(times[-1] + (times[-1] - times[0]) / max(1, len(times) - 1))
    durations = [max(20, round((b - a) / speed * 1000)) for a, b in zip(times, times[1:])]
    CACHE_DIR.mkdir(parents=True, exist_ok=True)
    images[0].save(out, "WEBP", save_all=True, append_images=images[1:], duration=durations,
                   loop=0 if loop else 1, quality=quality, method=4)
    meta = {"w": images[0].width, "h": images[0].height, "seconds": sum(durations) / 1000}
    meta_path.write_text(json.dumps(meta))
    return out, meta


def _image_meta(path: Path) -> dict:
    from PIL import Image
    with Image.open(path) as im:
        seconds = 0.0
        for k in range(getattr(im, "n_frames", 1)):
            im.seek(k)
            seconds += (im.info.get("duration") or 100) / 1000
        return {"w": im.width, "h": im.height, "seconds": seconds}


class _NativeFrames:
    """Frames of the embedded clip for native manim rendering, decoded on
    demand (animated image: Pillow; WebM: PyAV, decoded once)."""

    def __init__(self, path: Path, anim: bool):
        self.path, self.anim = Path(path), anim
        self.starts, self._frames, self._im, self._last = [], None, None, None
        if anim:
            from PIL import Image
            self._im = Image.open(self.path)
            t = 0.0
            for k in range(getattr(self._im, "n_frames", 1)):
                self._im.seek(k)
                self.starts.append(t)
                t += (self._im.info.get("duration") or 100) / 1000
            self.seconds = t
        else:
            self._frames = [img for _, img in _source_frames(self.path) if img is not None]
            fps = len(self._frames) / max(1e-6, probe(self.path)["seconds"])
            self.starts = [k / fps for k in range(len(self._frames))]
            self.seconds = len(self._frames) / fps

    def __deepcopy__(self, memo):   # mobject copies (FadeIn, .animate, ...) share the decoder
        return self

    def index_at(self, t: float, loop: bool) -> int:
        t = t % self.seconds if loop and self.seconds > 0 else min(t, self.seconds)
        return max(0, int(np.searchsorted(self.starts, t, side="right")) - 1)

    def rgba(self, k: int) -> np.ndarray:
        """Frame k as a fresh RGBA array (the last one is cached)."""
        if self._last is None or self._last[0] != k:
            if self._im is not None:
                self._im.seek(k)
                img = self._im
            else:
                img = self._frames[k]
            self._last = (k, np.array(img.convert("RGBA")))
        return self._last[1].copy()


class VideoMobject(ImageMobject):
    """A video clip on the slide; see the module docstring for the options."""

    def __init__(self, source, width: float = 8.0, *, crop=None, trim=None, height: int = 720,
                 fps=None, speed: float = 1.0, loop: bool = False, crf: int = 34,
                 embed: str = "webp", quality: int = 80, **kwargs):
        source = Path(source)
        self.video_loop = loop
        if embed == "webp":
            # an animated image is embedded as-is unless it must change; a clip that
            # should play once (loop=False) is re-encoded, as GIFs usually loop forever
            untouched = (source.suffix.lower() in (".webp", ".gif") and not (crop or trim or speed != 1.0)
                         and loop and (height is None or _image_meta(source)["h"] <= height))
            if untouched:                                    # already an animated image: embed as-is
                path, meta = source.resolve(), _image_meta(source)
            else:
                path, meta = encode_animated_webp(source, crop, trim, height, speed, loop, quality)
            self.video_anim = True
        elif embed == "webm":
            meta = None
            if source.suffix.lower() == ".webm" and not (crop or trim or fps or speed != 1.0):
                meta = probe(source)                         # a WebM that is already small enough
                if height is not None and meta["h"] > height:
                    meta = None                              # ... or needs scaling down
            if meta is not None:
                path = source.resolve()
            else:
                path, meta = encode_webm(source, crop, trim, height, fps, speed, crf)
            self.video_anim = False
        else:
            raise ValueError(f"embed must be 'webp' or 'webm', not {embed!r}")
        self.video_path = str(path)
        self.video_seconds = meta["seconds"]          # handy for deck.wait(...)

        self._native = _NativeFrames(path, self.video_anim)
        super().__init__(self._native.rgba(0), **kwargs)
        self.scale_to_fit_width(width)
        self._clock = 0.0
        self.native_playback = True   # the recorder turns this off: the browser plays the clip

        def advance(m, dt):           # native manim only: show the frame for the time on screen
            if not m.native_playback or dt <= 0:
                return
            m._clock += dt          # (re-set every tick: a running fade rewrites the pixels)
            m.set_frame(m._native.index_at(m._clock, m.video_loop))
        advance.bridge_native = True  # the recorder ignores this updater
        self.add_updater(advance)

    def set_frame(self, k: int):
        """Show frame k (keeps the current opacity)."""
        rgba = self._native.rgba(k)
        self.orig_alpha_pixel_array = rgba[:, :, 3].copy()
        rgba[:, :, 3] = (rgba[:, :, 3] * float(self.stroke_opacity)).astype(np.uint8)
        self.pixel_array = rgba
        return self
