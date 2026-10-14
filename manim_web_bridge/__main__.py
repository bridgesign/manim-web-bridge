"""
Command line:  python -m manim_web_bridge <file.py> <SceneName> [out]

Imports the file (its folder is put on sys.path so it can import siblings),
runs the scene's construct() with recording instead of rendering, and writes
  out/          a folder: out/main.html + steps/ + media/ (default: <SceneName>/)
  out.html      one self-contained HTML file
"""

import argparse
import importlib.util
import sys
import tempfile
import time
from pathlib import Path


def main():
    ap = argparse.ArgumentParser(prog="python -m manim_web_bridge")
    ap.add_argument("file", type=Path)
    ap.add_argument("scene")
    ap.add_argument("out", nargs="?", type=Path)
    args = ap.parse_args()

    from manim import config
    # manim still caches LaTeX / text outlines on disk; keep that out of the project
    config.media_dir = str(Path(tempfile.gettempdir()) / "manim-web-bridge-media")
    config.verbosity = "WARNING"

    from .export import write_html, write_site

    src = args.file.resolve()
    sys.path.insert(0, str(src.parent))
    spec = importlib.util.spec_from_file_location(src.stem, src)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    scene_cls = getattr(module, args.scene)

    t = time.time()
    data = scene_cls().record()
    out = args.out or src.with_name(args.scene)
    if out.suffix == ".html":
        write_html(data, out, title=args.scene)
        size, page = out.stat().st_size, out
    else:
        page = write_site(data, out, title=args.scene)
        size = sum(f.stat().st_size for f in out.rglob("*") if f.is_file())
    steps, states = len(data["steps"]), len(data["states"])
    print(f"{page}: {steps} steps, {states} states, {size / 1e6:.1f} MB ({time.time() - t:.1f}s)")


if __name__ == "__main__":
    main()
