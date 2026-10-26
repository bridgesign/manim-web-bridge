"""
A live server for a slide deck: per-slide recording, a cache, hot reload.

    python -m manim_web_bridge.serve path/to/deck.py FullUpdate [--port 8000] [--workers 4]

then open http://localhost:8000. The scene class lists its slides
(`slides = [...]`, see BridgeScene); every slide is recorded on its own, in
worker processes (fresh imports every time, several at once), and cached on
disk next to the deck (.bridge-cache/) with the files it depended on:

  * its module and everything it imports from your own code (not Python's or
    site-packages' -- your editable packages, e.g. the template, count);
  * every file it OPENED while recording (images, GIFs, JSON ...), caught
    with an audit hook.

The server watches those files. Save one and only the slides that depend on
it are recorded again; the page, still open, is told (Server-Sent Events),
fetches the new deck and stays on the slide you are looking at. A change to
player.js reloads the page. A slide that fails keeps its last good version,
and the error is shown over the page until it is fixed.

Nothing is written next to the deck but the cache: the page, the steps and
the media are served from memory (clips from where they are on disk; a clip
that does not exist is looked up in <deck dir>/media/ and shows a
placeholder until it is there).

Slides recorded on their own do not see each other's objects, so the
player fades the previous slide out at each slide boundary (what the
template's own transition did), keeping what is identical on both sides
(footer, logos) on screen.
"""

from __future__ import annotations

import argparse
import base64
import hashlib
import json
import os
import queue
import re
import subprocess
import sys
import sysconfig
import tempfile
import threading
import time
import traceback
import uuid
from concurrent.futures import ThreadPoolExecutor
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import parse_qs, unquote, urlparse

HERE = Path(__file__).parent
FORMAT = 2                                    # bump when the cached segment format changes
IGNORED_DIRS = {"__pycache__", ".git", ".bridge-cache", "node_modules", ".ipynb_checkpoints"}


# ---------------------------------------------------------------------------
# which files are "yours"
# ---------------------------------------------------------------------------
_SYSTEM = [Path(p).resolve() for p in {sysconfig.get_paths()[k] for k in ("stdlib", "platstdlib", "purelib", "platlib")}]


def is_project_file(path) -> bool:
    """A file of your own code (not Python's, not an installed package's;
    editable installs live in your folders and count as yours)."""
    try:
        p = Path(path).resolve()
    except OSError:
        return False
    if not p.is_file() or any(part in IGNORED_DIRS for part in p.parts):
        return False
    return not any(p.is_relative_to(s) for s in _SYSTEM) and not str(p).startswith(tempfile.gettempdir())


def file_hash(path) -> str | None:
    try:
        return hashlib.sha1(Path(path).read_bytes()).hexdigest()[:16]
    except OSError:
        return None


def module_closure(module, seen: set | None = None) -> set:
    """Files of your own code a module uses: itself and, recursively, the
    modules of everything it imports (functions, classes, modules)."""
    import inspect
    seen = set() if seen is None else seen
    path = getattr(module, "__file__", None)
    if not path or not is_project_file(path) or path in seen:
        return seen
    seen.add(str(Path(path).resolve()))
    for value in list(vars(module).values()):
        mod = value if inspect.ismodule(value) else inspect.getmodule(value)
        if mod is not None and mod is not module:
            module_closure(mod, seen)
    return seen


# ---------------------------------------------------------------------------
# worker: record some slides in this (fresh) process
# ---------------------------------------------------------------------------
def load_scene_class(deck: Path, name: str):
    import importlib.util
    deck = Path(deck).resolve()
    sys.path.insert(0, str(deck.parent))
    spec = importlib.util.spec_from_file_location(deck.stem, deck)
    module = importlib.util.module_from_spec(spec)
    sys.modules[deck.stem] = module
    spec.loader.exec_module(module)
    return getattr(module, name)


def slide_key(fn) -> str:
    return f"{getattr(fn, '__module__', '?')}:{getattr(fn, '__qualname__', repr(fn))}"


def worker_plan(deck: Path, name: str, cls=None) -> dict:
    """The slide list and, for each slide, the files its code comes from."""
    import inspect
    cls = cls or load_scene_class(deck, name)
    # what every slide runs besides its own code: the deck file itself and the
    # scene class's base classes (the template, the bridge) with what they import
    # -- not the deck's other slides, which it imports too
    deck_module = sys.modules[cls.__module__]
    base = {str(Path(deck_module.__file__).resolve())}
    for c in cls.__mro__:
        mod = sys.modules.get(c.__module__)
        if mod is not None and mod is not deck_module:
            module_closure(mod, base)
    everything = module_closure(deck_module)                           # for watching: the deck and all it imports
    bridge = {str(p.resolve()) for p in HERE.glob("*.py")}
    slides = []
    for i, fn in enumerate(cls.slides):
        mod = inspect.getmodule(fn)
        files = module_closure(mod) if mod else set()
        try:
            where = {"file": str(Path(inspect.getsourcefile(fn)).resolve()), "function": fn.__name__}
        except (TypeError, OSError):
            where = {"file": None, "function": getattr(fn, "__name__", None)}
        # a slide runs its own module's code AND the scene class's (the deck, the
        # template, the bridge), so all of them count
        slides.append({"index": i, "key": slide_key(fn), "code": sorted(files | base | bridge), **where})
    return {"slides": slides, "deck_files": sorted(everything | bridge),
            "quick": bool(getattr(cls, "quick", False))}


def track_sources(root: Path) -> None:
    """Remember, on every mobject, where it was made: the call sites inside
    `root` (the deck's folder), innermost first, e.g. [[diagram.py, 22],
    [slide.py, 74]] for a box made by a helper. Copies keep their original's."""
    from manim import Mobject
    root = str(Path(root).resolve())
    original = Mobject.__init__
    if getattr(original, "_bridge_tracking", False):
        return

    def init(self, *args, **kwargs):
        original(self, *args, **kwargs)
        if "_bridge_src" in self.__dict__:
            return
        chain, f = [], sys._getframe(1)
        while f is not None and len(chain) < 4:
            path = f.f_code.co_filename
            if path.startswith(root) and ".bridge-cache" not in path:
                if not chain or chain[-1] != [path, f.f_lineno]:
                    chain.append([path, f.f_lineno])
            f = f.f_back
        self._bridge_src = chain or None
    init._bridge_tracking = True
    Mobject.__init__ = init


def worker_record(deck: Path, name: str, jobs: list, cache: Path, slot: int = 0) -> None:
    """Record slides [(index, numbered_before), ...] and write one cache file each."""
    from manim import config
    config.verbosity = "WARNING"
    config.media_dir = str(cache / f"manim-media-{slot}")             # LaTeX/text cache, one per worker slot
    opened: set = set()
    recording = [False]

    def audit(event, args):
        if recording[0] and event == "open" and args and isinstance(args[0], (str, bytes, os.PathLike)):
            path = os.fsdecode(args[0])
            mode = args[1] if len(args) > 1 else "r"
            if isinstance(mode, str) and "r" in mode and "w" not in mode and is_project_file(path):
                opened.add(str(Path(path).resolve()))
    sys.addaudithook(audit)

    track_sources(Path(deck).resolve().parent)
    cls = load_scene_class(deck, name)
    plan = {s["index"]: s for s in worker_plan(deck, name, cls)["slides"]}
    for index, numbered_before in jobs:
        out = cache / f"slide_{index:03d}.json"
        opened.clear()
        try:
            recording[0] = True
            data = cls().record_slide(index, numbered_before, inline_media=False)
            recording[0] = False
            files = set(plan[index]["code"]) | opened
            result = {"format": FORMAT, "index": index, "key": plan[index]["key"], "numbered_before": numbered_before,
                      "deps": {f: file_hash(f) for f in sorted(files)}, "data": data}
        except Exception:
            recording[0] = False
            result = {"format": FORMAT, "index": index, "key": plan[index]["key"], "numbered_before": numbered_before,
                      "error": traceback.format_exc(limit=12),
                      "deps": {f: file_hash(f) for f in sorted(set(plan[index]["code"]) | opened)}}
        tmp = out.with_suffix(".tmp")
        tmp.write_text(json.dumps(result))
        tmp.replace(out)


# ---------------------------------------------------------------------------
# merging slides into one deck
# ---------------------------------------------------------------------------
def merge(segments: list) -> dict:
    """Slides recorded on their own -> one deck in the recorder's format.
    Identical outlines, states, images and clips are shared (so the footer
    is the same state on every slide); object ids are made unique; each step
    says which slide it belongs to and whether it starts one."""
    geoms, gidx, states, sidx = [], {}, [], {}
    images, iidx, videos, vidx = [], {}, [], {}
    names, sources, owners, steps = [], [], [], []

    def intern(table, index, value):
        key = json.dumps(value, sort_keys=True, separators=(",", ":"))
        if key not in index:
            index[key] = len(table)
            table.append(value)
        return index[key]

    for si, seg in enumerate(segments):
        d = seg["data"]
        uoff = len(names)
        names += d.get("names", [])
        sources += d.get("sources") or [None] * len(d.get("names", []))
        owners += [si] * len(d.get("names", []))
        gmap = [intern(geoms, gidx, g) for g in d["geoms"]]
        imap = [intern(images, iidx, u) for u in d["images"]]
        vmap = [intern(videos, vidx, v) for v in d.get("videos", [])]
        smap = []
        for st in d["states"]:
            st = dict(st)
            if "g" in st:
                st["g"] = gmap[st["g"]]
            if "img" in st:
                st["img"] = imap[st["img"]]
            if "vid" in st:
                st["vid"] = vmap[st["vid"]]
            smap.append(intern(states, sidx, st))
        pair = lambda p: [p[0] + uoff, smap[p[1]]]
        for k, step in enumerate(d["steps"]):
            cmds = []
            for c in step["cmds"]:
                c = dict(c)
                if c["op"] == "set":
                    c["d"] = [pair(p) for p in c["d"]]
                elif c["op"] == "play":
                    c["tr"] = [{**t, "u": t["u"] + uoff, "a": smap[t["a"]], "b": smap[t["b"]]} for t in c["tr"]]
                elif c["op"] == "frames":
                    c["f"] = [{**f, "u": [pair(p) for p in f["u"]], **({"o": [u + uoff for u in f["o"]]} if "o" in f else {})}
                              for f in c["f"]]
                cmds.append(c)
            steps.append({**step, "cmds": cmds, "end": [pair(p) for p in step["end"]], "slide": si, "first": k == 0})
    first = segments[0]["data"] if segments else {"frame": [14.2222, 8], "background": "#ffffff"}
    return {"version": 2, "frame": first["frame"], "background": first["background"], "geoms": geoms,
            "states": states, "images": images, "videos": videos, "steps": steps, "names": names,
            "sources": sources, "owners": owners}


def step_chunk(deck: dict, k: int, build: str) -> dict:
    """Step k with every state and outline it uses (self-contained)."""
    step = deck["steps"][k]
    refs = set()
    for c in step["cmds"]:
        if c["op"] == "set":
            refs |= {s for _, s in c["d"]}
        elif c["op"] == "play":
            refs |= {t["a"] for t in c["tr"]} | {t["b"] for t in c["tr"]}
        elif c["op"] == "frames":
            refs |= {s for f in c["f"] for _, s in f["u"]}
    refs |= {s for _, s in step["end"]}
    states = {s: deck["states"][s] for s in sorted(refs)}
    geoms = {st["g"]: deck["geoms"][st["g"]] for st in states.values() if "g" in st}
    return {"build": build, "step": step, "states": states, "geoms": geoms}


# ---------------------------------------------------------------------------
# the server's model of the deck
# ---------------------------------------------------------------------------
class Deck:
    def __init__(self, path: Path, name: str, workers: int = 4, log=print):
        self.path, self.name = Path(path).resolve(), name
        self.cache = self.path.parent / ".bridge-cache" / name
        self.cache.mkdir(parents=True, exist_ok=True)
        self.workers, self.log = workers, log
        self.lock = threading.Lock()
        self.plan: dict = {}
        self.segments: dict = {}                     # index -> cached result (ok or error)
        self.deck: dict | None = None
        self.build = ""
        self.errors: dict = {}                       # index -> traceback
        self.media: dict = {}                        # url path -> ("bytes", data, type) | ("file", path)
        self.listeners: list = []                    # SSE queues

    # -- events ----------------------------------------------------------
    def broadcast(self, event: str, payload: dict):
        for q in list(self.listeners):
            q.put((event, payload))

    # -- running workers -------------------------------------------------------
    def _run_worker(self, args: list) -> subprocess.CompletedProcess:
        cmd = [sys.executable, "-m", "manim_web_bridge.serve", "--worker", str(self.path), self.name] + args
        return subprocess.run(cmd, capture_output=True, text=True, cwd=str(self.path.parent))

    def refresh_plan(self) -> bool:
        out = self.cache / "plan.json"
        r = self._run_worker(["--plan", str(out)])
        if r.returncode != 0:
            self.errors["plan"] = r.stderr[-4000:]
            self.broadcast("error", {"slide": None, "message": "the deck does not load:\n" + r.stderr[-3000:]})
            self.log("plan failed:\n" + r.stderr[-2000:])
            return False
        self.errors.pop("plan", None)
        self.plan = json.loads(out.read_text())
        return True

    def _cached(self, index: int):
        f = self.cache / f"slide_{index:03d}.json"
        return json.loads(f.read_text()) if f.exists() else None

    def _valid(self, seg, slide, numbered_before) -> bool:
        """A cached slide is still good (a failed one is always tried again)."""
        return (seg is not None and "error" not in seg and seg.get("format") == FORMAT and seg.get("key") == slide["key"]
                and seg.get("numbered_before") == numbered_before
                and all(file_hash(f) == h for f, h in seg["deps"].items()))

    def record(self, jobs: list) -> None:
        """Record [(index, numbered_before)] in up to `workers` processes."""
        if not jobs:
            return
        n = max(1, min(self.workers, len(jobs)))
        groups = [jobs[i::n] for i in range(n)]
        t0 = time.time()
        with ThreadPoolExecutor(n) as pool:
            results = list(pool.map(lambda ig: self._run_worker(
                ["--slides", json.dumps(ig[1]), "--cache", str(self.cache), "--slot", str(ig[0])]), enumerate(groups)))
        for r in results:
            if r.returncode != 0:
                self.log("worker failed:\n" + r.stderr[-2000:])
        self.log(f"recorded {len(jobs)} slide(s) in {time.time() - t0:.1f}s")

    def rebuild(self, changed: set | None = None) -> None:
        """Bring every slide up to date (re-plan, record what is stale), merge, tell the pages."""
        with self.lock:
            if not self.refresh_plan():
                return
            slides = self.plan["slides"]
            # numbering: each slide starts after the numbered slides before it
            tried = set()                                           # (slide, numbering) recorded in this rebuild
            for _ in range(3):                                      # settles in one pass, normally
                jobs, numbered, before = [], {}, 0
                for s in slides:
                    seg = self._cached(s["index"])
                    if not self._valid(seg, s, before) and (s["index"], before) not in tried:
                        jobs.append((s["index"], before))
                        tried.add((s["index"], before))
                    numbered[s["index"]] = before
                    made = (seg or {}).get("data", {}).get("numbered") if self._valid(seg, s, before) else None
                    before += made if made is not None else 1
                if not jobs:
                    break
                self.broadcast("building", {"slides": [i for i, _ in jobs]})
                self.record(jobs)
            segs, self.errors = [], {k: v for k, v in self.errors.items() if k == "plan"}
            self.seg_slides = []                                    # plan entry of each merged slide
            for s in slides:
                seg = self._cached(s["index"])
                if seg and "error" in seg:
                    self.errors[s["index"]] = seg["error"]
                    good = self.segments.get(s["key"])              # keep the last good version
                    if good:
                        segs.append(good)
                        self.seg_slides.append(s)
                    continue
                if seg:
                    self.segments[s["key"]] = seg
                    segs.append(seg)
                    self.seg_slides.append(s)
            self.deck = merge(segs)
            self._index_media()
            self.build = uuid.uuid4().hex[:12]
            errors = len([k for k in self.errors if k != "plan"])
            self.log(f"build {self.build}: {len(segs)} slides, {len(self.deck['steps'])} steps"
                     + (f", {errors} slide(s) failing (last good version shown)" if errors else ""))
            self.broadcast("build", {"build": self.build})
            for index, tb in self.errors.items():
                if index != "plan":
                    self.broadcast("error", {"slide": index, "message": f"slide {index + 1} failed:\n{tb}"})

    def _index_media(self) -> None:
        """Images (data URLs) and clips (files) -> URLs this server answers."""
        media = {}
        urls = []
        for u in self.deck["images"]:
            head, b64 = u.split(",", 1)
            raw = base64.b64decode(b64)
            ext = head.split("/")[1].split(";")[0]
            path = f"/media/img/{hashlib.sha1(raw).hexdigest()[:16]}.{ext}"
            media[path] = ("bytes", raw, f"image/{ext}")
            urls.append(path)
        clips, used = [], set()
        for v in self.deck["videos"]:
            name, k = v["name"], 2
            while name in used:
                name, k = f"{Path(v['name']).stem}_{k}{Path(v['name']).suffix}", k + 1
            used.add(name)
            src = v.get("url") or ""
            if src.startswith("file:"):
                media[f"/media/{name}"] = ("file", src[5:])
            elif src.startswith("data:"):
                head, b64 = src.split(",", 1)
                media[f"/media/{name}"] = ("bytes", base64.b64decode(b64), head[5:].split(";")[0])
            else:                                                   # missing: <deck dir>/media/<name>
                media[f"/media/{name}"] = ("file", str(self.path.parent / "media" / v["name"]))
            clips.append({"name": name, "kind": v["kind"], "loop": v["loop"], "url": f"/media/{name}"})
        self.media, self.head_images, self.head_videos = media, urls, clips

    def head(self) -> dict:
        d = self.deck
        return {"version": 2, "server": True, "build": self.build, "frame": d["frame"], "background": d["background"],
                "images": self.head_images, "videos": self.head_videos,
                "deps": [[k] for k in range(len(d["steps"]))],
                "slides": [s["slide"] for s in d["steps"]], "notes": [s.get("note", "") for s in d["steps"]],
                "numbers": [s.get("number") for s in d["steps"]]}

    def watched_files(self) -> set:
        files = set(self.plan.get("deck_files", []))
        for f in self.cache.glob("slide_*.json"):
            try:
                files |= set(json.loads(f.read_text())["deps"])
            except (OSError, ValueError, KeyError):
                pass
        files.add(str(HERE / "player.js"))
        return files


# ---------------------------------------------------------------------------
# editing code (the page's editor)
# ---------------------------------------------------------------------------
def text_hash(text: str) -> str:
    return hashlib.sha1(text.encode()).hexdigest()[:16]


def code_range(path: Path, line: int | None = None, function: str | None = None):
    """(start, end) lines (1-based, inclusive) of what to edit in `path`: the
    top-level function `function` (a whole slide, decorators included), or
    the smallest statement containing `line` (one item)."""
    import ast
    tree = ast.parse(path.read_text())
    if function:
        for node in tree.body:
            if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)) and node.name == function:
                start = min([node.lineno] + [d.lineno for d in node.decorator_list])
                return start, node.end_lineno
        raise LookupError(f"no function {function}() in {path.name}")
    best = None
    for node in ast.walk(tree):
        if isinstance(node, ast.stmt) and node.lineno <= line <= node.end_lineno:
            if best is None or (node.end_lineno - node.lineno) < (best.end_lineno - best.lineno) or (
                    node.end_lineno - node.lineno == best.end_lineno - best.lineno and node.lineno >= best.lineno):
                if not isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)) or best is None:
                    best = node
    if best is None:
        raise LookupError(f"line {line} of {path.name} is not in a statement")
    return best.lineno, best.end_lineno


def lint(path: Path, start: int, end: int, code: str) -> list:
    """Problems in `code` placed at lines start..end of `path`, as
    [{line, col, message, severity}] with lines counted inside `code`:
    syntax errors always, and pyflakes' warnings when it is installed."""
    lines = path.read_text().split("\n")
    new = "\n".join(lines[:start - 1] + code.split("\n") + lines[end:])
    out = []
    try:
        compile(new, str(path), "exec")
    except SyntaxError as e:
        out.append({"line": (e.lineno or start) - start + 1, "col": max(0, (e.offset or 1) - 1),
                    "message": e.msg, "severity": "error"})
        return out
    try:
        from pyflakes import api as pyflakes_api, reporter
        class Collect(reporter.Reporter):
            def __init__(self):
                pass
            def flake(self, m):
                if start <= m.lineno <= start + code.count("\n"):
                    out.append({"line": m.lineno - start + 1, "col": getattr(m, "col", 0),
                                "message": m.message % m.message_args, "severity": "warning"})
            def syntaxError(self, *a):
                pass
            def unexpectedError(self, *a):
                pass
        pyflakes_api.check(new, str(path), Collect())
    except ImportError:
        pass
    return out


# -- inserting code: slide functions in sections ------------------------------
SECTIONS = ("Objects", "Groups", "Transitions")
MARKER = re.compile(r"^\s*## (Objects|Groups|Transitions)\b")


def _function(tree, name):
    import ast
    for node in tree.body:
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)) and node.name == name:
            return node
    raise LookupError(f"no function {name}()")


def function_end(lines: list, fn) -> int:
    """The function's last line (1-based), counting the comment lines after
    its last statement that still belong to it (indented like its body) --
    ast stops at the last statement, which would leave the sections out."""
    indent = " " * fn.body[0].col_offset
    end = fn.end_lineno
    i = end
    while i < len(lines) and (not lines[i].strip() or lines[i].startswith(indent)):
        i += 1
        if lines[i - 1].strip():
            end = i
    return end


def ensure_sections(text: str, function: str) -> str:
    """Give the slide function its `## Objects / ## Groups / ## Transitions`
    comment sections (appended at its end, in that order, if missing)."""
    import ast
    lines = text.split("\n")
    fn = _function(ast.parse(text), function)
    end = function_end(lines, fn)
    have = {m.group(1) for ln in lines[fn.lineno - 1:end] if (m := MARKER.match(ln))}
    missing = [s for s in SECTIONS if s not in have]
    if not missing:
        return text
    indent = " " * fn.body[0].col_offset
    add = [""] + [f"{indent}## {s}" for s in missing]
    return "\n".join(lines[:end] + add + lines[end:])


def insert_into_section(text: str, function: str, section: str, code: list) -> str:
    """Add `code` lines at the end of `section` of the slide function."""
    import ast
    text = ensure_sections(text, function)
    lines = text.split("\n")
    fn = _function(ast.parse(text), function)
    end = function_end(lines, fn)
    body = range(fn.lineno - 1, end)                              # 0-based line indexes of the function
    marks = {MARKER.match(lines[i]).group(1): i for i in body if MARKER.match(lines[i])}
    start = marks[section]
    stop = min([i for i in marks.values() if i > start] + [end])
    last = max([i for i in range(start, stop) if lines[i].strip()] + [start])   # after the section's last line
    indent = " " * fn.body[0].col_offset
    return "\n".join(lines[:last + 1] + [indent + c for c in code] + lines[last + 1:])


def ensure_imports(text: str, module: str, names: list) -> str:
    """`from module import ...` for the names the file does not have yet."""
    import ast
    tree = ast.parse(text)
    have = {a.asname or a.name for n in ast.walk(tree) if isinstance(n, (ast.Import, ast.ImportFrom)) for a in n.names}
    missing = sorted(set(names) - have)
    if not missing:
        return text
    lines = text.split("\n")
    same = [n for n in tree.body if isinstance(n, ast.ImportFrom) and n.module == module and n.lineno == n.end_lineno
            and not any(a.asname for a in n.names)]
    if same:                                                    # extend `from module import a, b` in place
        n = same[0]
        merged = sorted({a.name for a in n.names} | set(missing), key=lambda x: (not x.isupper(), x))
        lines[n.lineno - 1] = f"from {module} import {', '.join(merged)}"
        return "\n".join(lines)
    imports = [n for n in tree.body if isinstance(n, (ast.Import, ast.ImportFrom))]
    at = imports[-1].end_lineno if imports else 0
    return "\n".join(lines[:at] + [f"from {module} import {', '.join(missing)}"] + lines[at:])


def statement_name(text: str, line: int) -> str | None:
    """The variable the statement at `line` assigns (`name = ...`), if any."""
    import ast
    best = None
    for node in ast.walk(ast.parse(text)):
        if isinstance(node, (ast.Assign, ast.AnnAssign)) and node.lineno <= line <= node.end_lineno:
            if best is None or node.lineno >= best.lineno:
                best = node
    if best is None:
        return None
    targets = best.targets if isinstance(best, ast.Assign) else [best.target]
    return targets[0].id if len(targets) == 1 and isinstance(targets[0], ast.Name) else None


def fresh_name(text: str, function: str, stem: str) -> str:
    import ast
    used = {n.id for n in ast.walk(_function(ast.parse(text), function)) if isinstance(n, ast.Name)}
    k = 1
    while f"{stem}_{k}" in used:
        k += 1
    return f"{stem}_{k}"


def ink_for(background: str) -> str:
    """A colour that shows on `background` (manim's shapes default to white or red)."""
    r, g, b = (int(background.lstrip("#")[i:i + 2], 16) for i in (0, 2, 4))
    return "#1F1F1F" if 0.299 * r + 0.587 * g + 0.114 * b > 140 else "#FFFFFF"


def object_code(kind: str, var: str, cv: str, fx: float, fy: float, value: str = "", ink: str = "#1F1F1F") -> tuple:
    """(code lines for ## Objects, the transition line, manim names to import),
    every size and position a fraction of the canvas; shapes drawn in `ink`."""
    at = f"{cv}.point({fx:.3f}, {fy:.3f})"
    if kind == "text":
        return [f"{var} = Text({value or 'Text'!r}, font_size={cv}.font(0.04)).move_to({at})"], f"FadeIn({var})", ["FadeIn", "Text"]
    if kind == "equation":
        return [f"{var} = MathTex(r{(value or 'x^2')!r}, font_size={cv}.font(0.05)).move_to({at})"], f"Write({var})", ["MathTex", "Write"]
    if kind == "box":
        return [f"{var} = RoundedRectangle(width={cv}.w(0.2), height={cv}.h(0.12), corner_radius={cv}.h(0.02), color='{ink}').move_to({at})"], f"FadeIn({var})", ["FadeIn", "RoundedRectangle"]
    if kind == "circle":
        return [f"{var} = Circle(radius={cv}.h(0.06), color='{ink}').move_to({at})"], f"FadeIn({var})", ["Circle", "FadeIn"]
    if kind == "arrow":
        return [f"{var} = Arrow({at}, {cv}.point({min(fx + 0.12, 0.98):.3f}, {fy:.3f}), buff=0, color='{ink}')"], f"GrowArrow({var})", ["Arrow", "GrowArrow"]
    if kind == "image":
        return [f"{var} = ImageMobject({value!r}).set_width({cv}.w(0.3)).move_to({at})"], f"FadeIn({var})", ["FadeIn", "ImageMobject"]
    raise ValueError(f"unknown kind {kind!r}")


# ---------------------------------------------------------------------------
# watching files
# ---------------------------------------------------------------------------
def watch(deck: Deck, interval: float = 0.5):
    """Poll what the deck depends on (plus new files in its folder); rebuild on change."""
    def snapshot():
        files = deck.watched_files()
        for p in deck.path.parent.rglob("*"):
            if p.is_file() and not any(part in IGNORED_DIRS for part in p.parts):
                files.add(str(p))
        out = {}
        for f in files:
            try:
                out[f] = os.stat(f).st_mtime_ns
            except OSError:
                out[f] = None
        return out

    last = snapshot()
    while True:
        time.sleep(interval)
        now = snapshot()
        changed = {f for f in now.keys() | last.keys() if now.get(f) != last.get(f)}
        if not changed:
            continue
        time.sleep(0.3)                                             # let the editor finish writing
        now = snapshot()
        changed = {f for f in now.keys() | last.keys() if now.get(f) != last.get(f)}
        last = now
        if changed == {str(HERE / "player.js")}:
            deck.log("player.js changed: reloading the pages")
            deck.broadcast("reload", {})
            continue
        deck.log("changed: " + ", ".join(sorted(Path(f).name for f in changed)[:6]) + (" ..." if len(changed) > 6 else ""))
        try:
            deck.rebuild(changed)
        except Exception:
            deck.log("rebuild failed:\n" + traceback.format_exc())
        last = snapshot()


# ---------------------------------------------------------------------------
# HTTP
# ---------------------------------------------------------------------------
EDITOR_TAGS = """
<link rel="stylesheet" href="/editor/vendor/codemirror.min.css">
<link rel="stylesheet" href="/editor/vendor/lint.min.css">
<script src="/editor/vendor/codemirror.min.js"></script>
<script src="/editor/vendor/python.min.js"></script>
<script src="/editor/vendor/matchbrackets.min.js"></script>
<script src="/editor/vendor/lint.min.js"></script>
<script type="module">
  // after the player has set window.bridge up
  const start = () => import("/editor/editor.js");
  window.bridge ? start() : addEventListener("bridge-ready", start, { once: true });
</script>
"""


def make_handler(deck: Deck):
    from .export import PAGE

    class Handler(BaseHTTPRequestHandler):
        def log_message(self, *args):                               # quiet
            pass

        def send(self, code: int, body: bytes, ctype: str, headers: dict | None = None):
            self.send_response(code)
            self.send_header("Content-Type", ctype)
            self.send_header("Content-Length", str(len(body)))
            self.send_header("Cache-Control", "no-store")
            for k, v in (headers or {}).items():
                self.send_header(k, v)
            self.end_headers()
            self.wfile.write(body)

        def json(self, value, code: int = 200):
            self.send(code, json.dumps(value).encode(), "application/json")

        def do_GET(self):
            url = urlparse(self.path)
            path = unquote(url.path)
            if path in ("/", "/index.html"):
                if deck.deck is None:
                    return self.send(503, b"recording the slides ... reload in a moment", "text/plain")
                w, h = deck.deck["frame"]
                page = PAGE.format(title=deck.name, background=deck.deck["background"], aspect=w / h,
                                   data_id="bridge-head", data_type="application/json",
                                   data=json.dumps(deck.head()).replace("</", "<\\/"),
                                   player=(HERE / "player.js").read_text())
                page = page.replace("</body>", EDITOR_TAGS + "</body>", 1)          # the code editor
                return self.send(200, page.encode(), "text/html; charset=utf-8")
            if path == "/api/head":
                return self.json(deck.head())
            if path.startswith("/api/step/"):
                k = int(path.rsplit("/", 1)[1])
                if deck.deck is None or not 0 <= k < len(deck.deck["steps"]):
                    return self.json({"error": "no such step"}, 404)
                return self.json(step_chunk(deck.deck, k, deck.build))
            if path == "/api/status":
                return self.json({"build": deck.build, "slides": len(deck.plan.get("slides", [])),
                                  "steps": len(deck.deck["steps"]) if deck.deck else 0,
                                  "errors": {str(k): v[-400:] for k, v in deck.errors.items()}})
            if path == "/api/events":
                return self.events()
            if path.startswith("/api/object/"):
                return self.json(self.object_info(int(path.rsplit("/", 1)[1])))
            if path == "/api/source":
                q = {k: v[0] for k, v in parse_qs(url.query).items()}
                return self.source(q)
            if path.startswith("/editor/"):
                f = (HERE / "editor" / path[len("/editor/"):]).resolve()
                if not f.is_relative_to((HERE / "editor").resolve()) or not f.is_file():
                    return self.send(404, b"not found", "text/plain")
                ctype = {".js": "text/javascript", ".css": "text/css"}.get(f.suffix, "application/octet-stream")
                return self.send(200, f.read_bytes(), ctype)
            if path.startswith("/media/"):
                item = deck.media.get(path)
                if item is None:
                    return self.send(404, b"not found", "text/plain")
                if item[0] == "bytes":
                    return self.send(200, item[1], item[2])
                f = Path(item[1])
                if not f.is_file():
                    return self.send(404, f"{f.name}: not there yet".encode(), "text/plain")
                import mimetypes
                return self.send(200, f.read_bytes(), mimetypes.guess_type(f.name)[0] or "application/octet-stream")
            self.send(404, b"not found", "text/plain")

        # -- the editor ---------------------------------------------------------
        def deck_file(self, rel: str) -> Path:
            """A file of the deck (only those can be read or written here)."""
            f = (deck.path.parent / rel).resolve()
            if not f.is_relative_to(deck.path.parent) or f.suffix != ".py" or not f.is_file():
                raise PermissionError(f"not a Python file of this deck: {rel}")
            return f

        def object_info(self, uid: int) -> dict:
            d = deck.deck
            if d is None or not 0 <= uid < len(d["names"]):
                return {"error": "no such object"}
            root = deck.path.parent
            chain = [[str(Path(f).relative_to(root)), n] for f, n in (d["sources"][uid] or [])
                     if Path(f).is_relative_to(root)]
            slide = deck.seg_slides[d["owners"][uid]]
            fn_file = slide.get("file")
            key = (d["names"][uid], json.dumps(d["sources"][uid]), d["owners"][uid])
            group = [u for u in range(len(d["names"]))                 # every part of the same item
                     if d["owners"][u] == key[2] and d["names"][u] == key[0] and json.dumps(d["sources"][u]) == key[1]]
            return {"uid": uid, "name": d["names"][uid], "sources": chain, "group": group, "root": str(root),
                    "slide": {"index": slide["index"], "function": slide.get("function"),
                              "file": str(Path(fn_file).relative_to(root)) if fn_file and Path(fn_file).is_relative_to(root) else None}}

        def source(self, q: dict):
            try:
                f = self.deck_file(q["file"])
                start, end = code_range(f, int(q["line"]) if q.get("line") else None, q.get("function"))
                text = f.read_text()
                return self.json({"file": q["file"], "start": start, "end": end, "sha": text_hash(text),
                                  "code": "\n".join(text.split("\n")[start - 1:end]), "total": text.count("\n") + 1})
            except (KeyError, LookupError, PermissionError, SyntaxError, ValueError) as e:
                return self.json({"error": str(e)}, 400)

        def group_info(self, uids: list) -> dict:
            """The variables that hold the selected items, in the slide's own file."""
            d = deck.deck
            names, unnamed, images, slides = [], [], False, set()
            for u in uids:
                if not 0 <= u < len(d["names"]):
                    continue
                slide = deck.seg_slides[d["owners"][u]]
                slides.add(d["owners"][u])
                images |= d["names"][u].startswith(("ImageMobject", "VideoMobject"))
                here = [(f, n) for f, n in (d["sources"][u] or []) if f == slide.get("file")]
                var = statement_name(Path(here[-1][0]).read_text(), here[-1][1]) if here else None
                if var and var not in names:
                    names.append(var)
                elif not var and d["names"][u] not in unnamed:
                    unnamed.append(d["names"][u])
            return {"names": names, "unnamed": unnamed, "type": "Group" if images else "VGroup",
                    "slides": sorted(slides)}

        def insert(self, body: dict):
            """Add an object (kind + where, as fractions) or a group to the slide on screen."""
            import ast
            slide = deck.seg_slides[int(body["slide"])]
            f = self.deck_file(str(Path(slide["file"]).relative_to(deck.path.parent)))
            fn_name, text = slide["function"], f.read_text()
            fn = _function(ast.parse(text), fn_name)
            scene = fn.args.args[0].arg if fn.args.args else "self"
            assigned = {n.id for n in ast.walk(fn) if isinstance(n, ast.Name) and isinstance(n.ctx, ast.Store)}
            if body["kind"] == "group":
                kind = "Group" if body.get("type") == "Group" else "VGroup"
                var = fresh_name(text, fn_name, "group")
                text = insert_into_section(text, fn_name, "Groups", [f"{var} = {kind}({', '.join(body['names'])})"])
                text = ensure_imports(text, "manim", [kind])
            else:
                cv = "cv" if "cv" in assigned else None
                pre = [] if cv else ["cv = Canvas()   # positions and sizes below are fractions of the canvas"]
                var = fresh_name(text, fn_name, body["kind"])
                code, anim, needs = object_code(body["kind"], var, "cv", float(body["fx"]), float(body["fy"]),
                                                body.get("value", ""), ink_for(deck.deck["background"]))
                text = insert_into_section(text, fn_name, "Objects", pre + code)
                text = insert_into_section(text, fn_name, "Transitions", [f"{scene}.play({anim})"])
                text = ensure_imports(text, "manim", needs)
                if not cv:
                    text = ensure_imports(text, "manim_web_bridge", ["Canvas"])
            compile(text, str(f), "exec")                               # never write code that does not parse
            tmp = f.with_suffix(".py.bridge-tmp")
            tmp.write_text(text)
            tmp.replace(f)
            deck.log(f"inserted from the page: {var} into {f.name}:{fn_name}()")
            return self.json({"ok": True, "name": var, "file": str(f.relative_to(deck.path.parent))})

        def do_POST(self):
            url = urlparse(self.path)
            try:
                body = json.loads(self.rfile.read(int(self.headers.get("Content-Length", 0))) or b"{}")
                if url.path == "/api/group":
                    return self.json(self.group_info([int(u) for u in body["uids"]]))
                if url.path == "/api/insert":
                    return self.insert(body)
            except (KeyError, ValueError, LookupError, PermissionError, SyntaxError) as e:
                return self.json({"error": str(e)}, 400)
            try:
                f = self.deck_file(body["file"])
                start, end, code = int(body["start"]), int(body["end"]), body["code"]
            except (KeyError, ValueError, PermissionError) as e:
                return self.json({"error": str(e)}, 400)
            if url.path == "/api/lint":
                return self.json({"problems": lint(f, start, end, code)})
            if url.path == "/api/source":
                text = f.read_text()
                if body.get("sha") != text_hash(text):
                    return self.json({"error": f"{body['file']} changed since it was opened; reopen to edit it"}, 409)
                problems = [p for p in lint(f, start, end, code) if p["severity"] == "error"]
                if problems:
                    return self.json({"error": "not saved: the code does not parse", "problems": problems}, 400)
                lines = text.split("\n")
                tmp = f.with_suffix(".py.bridge-tmp")
                tmp.write_text("\n".join(lines[:start - 1] + code.split("\n") + lines[end:]))
                tmp.replace(f)
                deck.log(f"edited from the page: {body['file']} lines {start}-{end}")
                return self.json({"ok": True})
            return self.json({"error": "unknown"}, 404)

        def events(self):
            """Server-Sent Events: build / error / reload."""
            q = queue.Queue()
            deck.listeners.append(q)
            self.send_response(200)
            self.send_header("Content-Type", "text/event-stream")
            self.send_header("Cache-Control", "no-store")
            self.end_headers()
            try:
                self.wfile.write(f"event: hello\ndata: {json.dumps({'build': deck.build})}\n\n".encode())
                for index, tb in deck.errors.items():
                    if index != "plan":
                        self.wfile.write(f"event: error\ndata: {json.dumps({'slide': index, 'message': f'slide {index + 1} failed:' + chr(10) + tb})}\n\n".encode())
                self.wfile.flush()
                while True:
                    try:
                        event, payload = q.get(timeout=15)
                        self.wfile.write(f"event: {event}\ndata: {json.dumps(payload)}\n\n".encode())
                    except queue.Empty:
                        self.wfile.write(b": keep-alive\n\n")
                    self.wfile.flush()
            except (BrokenPipeError, ConnectionResetError, OSError):
                pass
            finally:
                deck.listeners.remove(q)

    return Handler


def serve(deck_path, name: str, port: int = 8000, workers: int = 4, host: str = "127.0.0.1") -> None:
    deck = Deck(deck_path, name, workers=workers)
    server = ThreadingHTTPServer((host, port), make_handler(deck))
    server.daemon_threads = True
    threading.Thread(target=server.serve_forever, daemon=True).start()
    print(f"serving {name} from {deck.path} on http://{host}:{port}  (recording; the page works once the first build is done)")
    deck.rebuild()
    threading.Thread(target=watch, args=(deck,), daemon=True).start()
    print(f"watching for changes -- open http://{host}:{port}")
    try:
        while True:
            time.sleep(3600)
    except KeyboardInterrupt:
        server.shutdown()


def main(argv=None):
    ap = argparse.ArgumentParser(prog="python -m manim_web_bridge.serve", description=__doc__.split("\n\n")[0])
    ap.add_argument("deck", type=Path, help="the Python file defining the scene")
    ap.add_argument("scene", help="the scene class (it lists its slides)")
    ap.add_argument("--port", type=int, default=8000)
    ap.add_argument("--host", default="127.0.0.1")
    ap.add_argument("--workers", type=int, default=max(1, min(8, (os.cpu_count() or 2) // 2)))
    ap.add_argument("--worker", action="store_true", help=argparse.SUPPRESS)
    ap.add_argument("--plan", type=Path, help=argparse.SUPPRESS)
    ap.add_argument("--slides", help=argparse.SUPPRESS)
    ap.add_argument("--cache", type=Path, help=argparse.SUPPRESS)
    ap.add_argument("--slot", type=int, default=0, help=argparse.SUPPRESS)
    a = ap.parse_args(argv)
    if a.worker:
        if a.plan:
            a.plan.write_text(json.dumps(worker_plan(a.deck, a.scene)))
        else:
            worker_record(a.deck, a.scene, [tuple(j) for j in json.loads(a.slides)], a.cache, a.slot)
        return
    serve(a.deck, a.scene, port=a.port, workers=a.workers, host=a.host)


if __name__ == "__main__":
    main()
