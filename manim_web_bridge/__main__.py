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
from pathlib import Path


def main():
    ap = argparse.ArgumentParser(prog="python -m manim_web_bridge")
    ap.add_argument("file", type=Path)
    ap.add_argument("scene")
    ap.add_argument("out", nargs="?", type=Path)
    args = ap.parse_args()

    from .export import export

    src = args.file.resolve()
    sys.path.insert(0, str(src.parent))
    spec = importlib.util.spec_from_file_location(src.stem, src)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    export(getattr(module, args.scene), args.out or src.with_name(args.scene))


if __name__ == "__main__":
    main()
