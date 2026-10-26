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


def _clip_entry(v: dict) -> dict:
    """A clip entry in the current form {name, kind, loop, url}; older
    recordings stored {"anim": url} (animated image) or {"src": url} (video)."""
    if "kind" in v:
        return v
    url = v.get("anim") or v.get("src")
    kind = "image" if "anim" in v else "video"
    ext = re.match(r"data:\w+/([\w.+-]+)", url or "")
    return {"name": f"clip.{ext.group(1) if ext else 'bin'}", "kind": kind, "loop": v.get("loop", False), "url": url}


STEP_HEADER = """\
// {title}
// Readable on purpose: change a timing (seconds), a position (x, y in frame units; the frame is
// {fw:g} x {fh:g} with 0, 0 at its centre), a colour ("#rrggbb") or an opacity (0 to 1), save and
// reload the page. A rebuild from Python writes this file again.
//
//   step.cmds  what this click does, in order:
//                "set"   what is on screen: [object, state] pairs
//                "play"  T seconds of tracks: k = kind (tween, create, write), u = object,
//                        a, b = start and end state, t = start time, d = duration, r = rate function
//                "wait"  d seconds; "frames" = sampled frames, fps per second
//   step.end   what is on screen when the step is done: [object, state]
//   states     the looks first used here: g = outline (in geoms; x, y place it), f, fo = fill colour
//              and opacity, c, co, w = stroke colour, opacity and width; img / vid = image / clip
//   geoms      outlines: 4 control points per curve, in 1/1000 frame units from the first point
"""


def _j(v) -> str:
    return json.dumps(v, ensure_ascii=False, separators=(", ", ": "))


def _commented(items, indent: str) -> str:
    """(code, comment) lines joined as a JSON list body, a comment after each."""
    out = []
    for i, (code, note) in enumerate(items):
        comma = "," if i < len(items) - 1 else ""
        out.append(f"{indent}{code}{comma}" + (f"  // {note}" if note else ""))
    return "\n".join(out)


def _by_name(entries, name_of):
    """Consecutive entries on the same object share one line."""
    groups = []
    for e in entries:
        n = name_of(e)
        if groups and groups[-1][1] == n:
            groups[-1][0].append(e)
        else:
            groups.append(([e], n))
    return [(", ".join(_j(e) for e in es), n) for es, n in groups]


def _readable_chunk(k: int, n: int, chunk: dict, names: list, state_names: dict, frame) -> str:
    """One step as commented JSON, one object per line (see STEP_HEADER)."""
    name = lambda u: names[u] if u < len(names) else ""
    step = chunk["step"]
    title = f"step {k} of {n - 1}" + (f" -- {step['note']}" if step.get("note") else "")
    cmds = []
    for cmd in step["cmds"]:
        if cmd["op"] == "set":
            body = _commented(_by_name(cmd["d"], lambda p: name(p[0])), "      ")
            cmds.append((f'{{"op": "set", "d": [\n{body}\n    ]}}', ""))
        elif cmd["op"] == "play":
            body = _commented(_by_name(cmd["tr"], lambda tr: name(tr["u"])), "      ")
            cmds.append((f'{{"op": "play", "T": {_j(cmd["T"])}, "tr": [\n{body}\n    ]}}', ""))
        else:
            cmds.append((_j(cmd), ""))
    flags = "".join(f'  "{key}": {_j(step[key])},\n' for key in ("note", "number", "loop", "auto") if key in step)
    end = _commented(_by_name(step["end"], lambda p: name(p[0])), "    ")
    states = _commented([(f'"{i}": {_j(v)}', state_names.get(int(i), "")) for i, v in chunk["states"].items()], "  ")
    geoms = _commented([(f'"{i}": {_j(v)}', "") for i, v in chunk["geoms"].items()], "  ")
    return (STEP_HEADER.format(title=title, fw=frame[0], fh=frame[1])
            + f"__bridgeChunk({k}, {{\n"
            + f'"build": {_j(chunk["build"])},\n'
            + '"step": {\n' + flags
            + '  "cmds": [\n' + _commented(cmds, "    ") + "\n  ],\n"
            + '  "end": [\n' + end + "\n  ]\n},\n"
            + '"states": {\n' + states + "\n},\n"
            + '"geoms": {\n' + geoms + "\n}\n});\n")


def _strip_comments(js: str) -> str:
    """Drop // comments outside strings (the readable step files carry them)."""
    out = []
    for line in js.splitlines():
        in_str = esc = False
        for i, ch in enumerate(line):
            if in_str:
                esc, in_str = (not esc and ch == "\\"), (in_str if esc or ch != '"' else False)
            elif ch == '"':
                in_str = True
            elif ch == "/" and line[i + 1: i + 2] == "/":
                line = line[:i]
                break
        out.append(line)
    return "\n".join(out)


def write_site(data: dict, folder, title: str = "Slides", readable: bool = True) -> Path:
    """Write the deck as a folder (see module docstring). Returns main.html.
    readable=True writes each step as commented JSON, one object per line,
    for people to read and edit; False writes it compact."""
    folder = Path(folder)
    shutil.rmtree(folder / "steps", ignore_errors=True)    # steps/ is the writer's alone
    (folder / "steps").mkdir(parents=True)
    media = folder / "media"                                # media/ is shared: files you add stay
    media.mkdir(parents=True, exist_ok=True)
    manifest = media / ".bridge-files"                      # what the last export wrote there
    if manifest.exists():
        previous = set(json.loads(manifest.read_text()))
    else:                                                   # an export from before the list existed
        previous = {p.name for p in media.glob("img_*")} | {p.name for p in media.glob("clip_*")}
    written, keep = set(), set()

    # media -> files; the header keeps only their relative URLs
    images = []
    for i, url in enumerate(data["images"]):
        raw, ext = _data_url_bytes(url)
        name = f"img_{i}.{ext}"
        (media / name).write_bytes(raw)
        written.add(name)
        images.append(f"media/{name}")
    videos = []
    for v in data.get("videos", []):
        v = _clip_entry(v)
        name = v["name"]
        if v["url"] is None:                                # missing at build time: point at media/<name>
            keep.add(name)
        else:                                               # the clip under its own name
            raw, _ = _data_url_bytes(v["url"])
            stem, suffix = Path(name).stem, Path(name).suffix
            k = 2
            while name in written:                          # two different clips, one name
                name, k = f"{stem}_{k}{suffix}", k + 1
            (media / name).write_bytes(raw)
            written.add(name)
        videos.append({"name": name, "kind": v["kind"], "loop": v["loop"], "url": f"media/{name}"})
    for old in previous - written - keep:                   # only files this writer made before
        (media / old).unlink(missing_ok=True)
    manifest.write_text(json.dumps(sorted(written)))

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
    names = data.get("names") or []
    state_names = {}                             # state -> the object it was first seen on
    for st in steps:
        for cmd in st["cmds"]:
            pairs = cmd.get("d", []) if cmd["op"] == "set" else []
            pairs += [[tr["u"], tr["a"]] for tr in cmd.get("tr", [])] + [[tr["u"], tr["b"]] for tr in cmd.get("tr", [])]
            for u, sid in pairs:
                state_names.setdefault(sid, names[u] if u < len(names) else "")
        for u, sid in st["end"]:
            state_names.setdefault(sid, names[u] if u < len(names) else "")
    for k, step in enumerate(steps):
        chunk = {"build": build, "step": step,
                 "states": {s: states[s] for s in range(len(states)) if s_owner[s] == k},
                 "geoms": {g: geoms[g] for g, o in g_owner.items() if o == k}}
        if readable:
            text = _readable_chunk(k, len(steps), chunk, names, state_names, data["frame"])
        else:
            text = f"__bridgeChunk({k},{json.dumps(chunk, separators=(',', ':'))});\n"
        (folder / "steps" / f"step_{k:03d}.js").write_text(text)

    head = {"version": 2, "build": build, "frame": data["frame"], "background": data["background"],
            "images": images, "videos": videos, "deps": deps}
    if any("number" in s for s in steps):        # which slide number each step begins (for #<slide>)
        head["numbers"] = [s.get("number") for s in steps]
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
            js = _strip_comments(js)
            chunk = json.loads(js[js.index(",", js.index("__bridgeChunk(")) + 1: js.rindex(")")])
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
def export(scene_class, out, title: str | None = None, quiet: bool = False, readable: bool = True) -> Path:
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
        page = write_site(data, out, title=title, readable=readable)
        size = sum(f.stat().st_size for f in out.rglob("*") if f.is_file())
    if not quiet:
        print(f"{page}: {len(data['steps'])} steps, {len(data['states'])} states, "
              f"{size / 1e6:.1f} MB ({time.time() - started:.1f}s)")
    return page
