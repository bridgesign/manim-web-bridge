"""
Write the recorded data + the player as a web page.

  write_site(data, "deck/")    a folder: main.html + one small script per
                               step + media files. The player loads a step's
                               data right before showing it and prefetches
                               the next one, so opening the deck is instant
                               however long it is. Works from file:// (the
                               chunks are <script> files, media are <img>
                               files: no fetch(), no server needed).
  write_html(data, "deck.html") ONE self-contained file (everything inlined,
                               gzipped), handy for sending a single file.
  read_html(path)              the recorded data back from either form.

No server, no CDN, no build step: the page is the data plus player.js
(plain JavaScript drawing on a <canvas>). The data -- mostly vector outlines
as numbers -- is gzipped and base64-encoded (~6x smaller); the browser
inflates it with its built-in DecompressionStream. compress=False writes
plain JSON instead (handy for debugging). read_html() reads either back.
"""

import base64
import gzip
import json
import re
import shutil
import uuid
from pathlib import Path

HERE = Path(__file__).parent

PAGE = """<!doctype html>
<html lang="en">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>{title}</title>
<style>
  html, body {{ margin: 0; height: 100%; background: {background}; overflow: hidden; }}
  body {{ display: flex; align-items: center; justify-content: center; }}
  /* 16:9 stage, as large as the window allows */
  #stage {{ display: block; width: min(100vw, calc(100vh * {aspect})); height: min(100vh, calc(100vw / {aspect})); }}
</style>
</head>
<body>
<canvas id="stage"></canvas>
<script id="{data_id}" type="{data_type}">{data}</script>
<script type="module">{player}</script>
</body>
</html>
"""


GZIP_TYPE = "application/gzip+base64"


def write_html(data: dict, path, title: str = "Slides", compress: bool = True) -> Path:
    path = Path(path)
    w, h = data["frame"]
    text = json.dumps(data, separators=(",", ":"))
    if compress:
        payload, data_type = base64.b64encode(gzip.compress(text.encode(), 9)).decode(), GZIP_TYPE
    else:
        payload, data_type = text.replace("</", "<\\/"), "application/json"
    path.write_text(PAGE.format(
        title=title,
        background=data["background"],
        aspect=w / h,
        data_id="bridge-data",
        data_type=data_type,
        data=payload,
        player=(HERE / "player.js").read_text(),
    ))
    return path


# ---------------------------------------------------------------------------
# Folder output: main.html + steps/step_NNN.js + media/
# ---------------------------------------------------------------------------
def _step_refs(step: dict) -> set:
    """State indices a step uses (its commands and its end snapshot)."""
    refs = {s for _, s in step["end"]}
    for c in step["cmds"]:
        if c["op"] == "set":
            refs |= {s for _, s in c["d"]}
        elif c["op"] == "play":
            refs |= {t["a"] for t in c["tr"]} | {t["b"] for t in c["tr"]}
        elif c["op"] == "frames":
            refs |= {s for f in c["f"] for _, s in f["u"]}
    return refs


def _data_url_bytes(url: str) -> tuple[bytes, str]:
    head, b64 = url.split(",", 1)
    mime = head[5:].split(";")[0]
    ext = {"image/png": "png", "image/webp": "webp", "image/jpeg": "jpg", "image/gif": "gif",
           "video/webm": "webm"}.get(mime, "bin")
    return base64.b64decode(b64), ext


def write_site(data: dict, folder, title: str = "Slides") -> Path:
    """Write the deck as a folder (see module docstring). Returns main.html."""
    folder = Path(folder)
    for sub in ("steps", "media"):              # only the folders this writer owns
        shutil.rmtree(folder / sub, ignore_errors=True)
        (folder / sub).mkdir(parents=True)

    # media -> files; the header keeps only their relative URLs
    images = []
    for i, url in enumerate(data["images"]):
        raw, ext = _data_url_bytes(url)
        (folder / "media" / f"img_{i}.{ext}").write_bytes(raw)
        images.append(f"media/img_{i}.{ext}")
    videos = []
    for i, v in enumerate(data.get("videos", [])):
        url = v.get("anim") or v.get("src")
        raw, ext = _data_url_bytes(url)
        (folder / "media" / f"clip_{i}.{ext}").write_bytes(raw)
        key = "anim" if "anim" in v else "src"
        videos.append({key: f"media/clip_{i}.{ext}", "loop": v.get("loop", False)})

    # every state / outline belongs to the first step that uses it
    states, geoms, steps = data["states"], data["geoms"], data["steps"]
    refs = [_step_refs(st) for st in steps]
    s_owner = [0] * len(states)
    seen = set()
    for k, r in enumerate(refs):
        for s in r - seen:
            s_owner[s] = k
        seen |= r
    g_owner = {}
    for s, st in enumerate(states):
        if "g" in st:
            g_owner[st["g"]] = min(g_owner.get(st["g"], s_owner[s]), s_owner[s])
    deps = []
    for k, r in enumerate(refs):
        need = {k} | {s_owner[s] for s in r} | {g_owner[states[s]["g"]] for s in r if "g" in states[s]}
        deps.append(sorted(need))
    build = uuid.uuid4().hex[:12]                # ties the steps to this main.html (see player.js)
    for k, step in enumerate(steps):
        chunk = {"build": build, "step": step,
                 "states": {s: states[s] for s in range(len(states)) if s_owner[s] == k},
                 "geoms": {g: geoms[g] for g, o in g_owner.items() if o == k}}
        (folder / "steps" / f"step_{k:03d}.js").write_text(
            f"__bridgeChunk({k},{json.dumps(chunk, separators=(',', ':'))});\n")

    head = {"version": 2, "build": build, "frame": data["frame"], "background": data["background"],
            "images": images, "videos": videos, "deps": deps}
    w, h = data["frame"]
    index = folder / "main.html"
    index.write_text(PAGE.format(
        title=title, background=data["background"], aspect=w / h,
        data_id="bridge-head", data_type="application/json",
        data=json.dumps(head, separators=(",", ":")).replace("</", "<\\/"),
        player=(HERE / "player.js").read_text(),
    ))
    return index


def read_html(path) -> dict:
    """The recorded data of an exported deck: a single .html file, or a
    folder / its main.html written by write_site."""
    path = Path(path)
    if path.is_dir():
        path = path / "main.html"
    text = path.read_text()
    head = re.search(r'<script id="bridge-head" type="application/json">(.*?)</script>', text, re.S)
    if head:
        h = json.loads(head.group(1).replace("<\\/", "</"))
        data = {"version": 2, "frame": h["frame"], "background": h["background"], "images": h["images"],
                "videos": h["videos"], "states": [], "geoms": [], "steps": []}
        states, geoms = {}, {}
        for k in range(len(h["deps"])):
            js = (path.parent / "steps" / f"step_{k:03d}.js").read_text()
            chunk = json.loads(js[js.index(",") + 1: js.rindex(")")])
            states.update({int(i): v for i, v in chunk["states"].items()})
            geoms.update({int(i): v for i, v in chunk["geoms"].items()})
            data["steps"].append(chunk["step"])
        data["states"] = [states[i] for i in range(len(states))]
        data["geoms"] = [geoms[i] for i in range(len(geoms))]
        return data
    m = re.search(r'<script id="bridge-data" type="([^"]+)">(.*?)</script>', text, re.S)
    data_type, payload = m.group(1), m.group(2)
    if data_type == GZIP_TYPE:
        return json.loads(gzip.decompress(base64.b64decode(payload)))
    return json.loads(payload.replace("<\\/", "</"))


# ---------------------------------------------------------------------------
# One call: record a scene and write it
# ---------------------------------------------------------------------------
def export(scene_class, out, title: str | None = None, quiet: bool = False) -> Path:
    """Record `scene_class` (a BridgeScene subclass) and write it to `out`:
    a folder (main.html + steps/ + media/) or, if `out` ends in .html, one
    self-contained file. Returns the page to open.

        from manim_web_bridge import export
        export(MyTalk, "talk/")            # or MyTalk.export("talk/")
    """
    import tempfile
    import time
    from manim import config

    # manim still caches LaTeX / text outlines on disk; keep that out of the project
    config.media_dir = str(Path(tempfile.gettempdir()) / "manim-web-bridge-media")
    config.verbosity = "WARNING"

    started = time.time()
    data = scene_class().record()
    out, title = Path(out), title or scene_class.__name__
    if out.suffix == ".html":
        page = write_html(data, out, title=title)
        size = out.stat().st_size
    else:
        page = write_site(data, out, title=title)
        size = sum(f.stat().st_size for f in out.rglob("*") if f.is_file())
    if not quiet:
        print(f"{page}: {len(data['steps'])} steps, {len(data['states'])} states, "
              f"{size / 1e6:.1f} MB ({time.time() - started:.1f}s)")
    return page
