"""manim-web-bridge: record manim scenes in Python, replay them in the browser (one offline HTML file, no video)."""

from .export import read_html, write_html
from .scene import BridgeScene

__all__ = ["BridgeScene", "read_html", "write_html"]
__version__ = "0.2.0"
