"""manim-web-bridge: record manim scenes in Python, replay them in the browser (one offline HTML file, no video)."""

from .export import write_html
from .scene import BridgeScene

__all__ = ["BridgeScene", "write_html"]
__version__ = "0.2.0"
