"""
Write the recorded data + the player into ONE self-contained HTML file.

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
<script id="bridge-data" type="{data_type}">{data}</script>
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
        data_type=data_type,
        data=payload,
        player=(HERE / "player.js").read_text(),
    ))
    return path


def read_html(path) -> dict:
    """The recorded data embedded in an exported page (compressed or not)."""
    m = re.search(r'<script id="bridge-data" type="([^"]+)">(.*?)</script>', Path(path).read_text(), re.S)
    data_type, payload = m.group(1), m.group(2)
    if data_type == GZIP_TYPE:
        return json.loads(gzip.decompress(base64.b64decode(payload)))
    return json.loads(payload.replace("<\\/", "</"))
