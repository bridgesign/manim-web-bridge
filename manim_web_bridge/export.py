"""
Write the recorded data + the player into ONE self-contained HTML file.

No server, no CDN, no build step: the page is the data (JSON) plus
player.js (plain JavaScript drawing on a <canvas>).
"""

import json
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
<script id="bridge-data" type="application/json">{data}</script>
<script>{player}</script>
</body>
</html>
"""


def write_html(data: dict, path, title: str = "Slides") -> Path:
    path = Path(path)
    w, h = data["frame"]
    path.write_text(PAGE.format(
        title=title,
        background=data["background"],
        aspect=w / h,
        data=json.dumps(data, separators=(",", ":")).replace("</", "<\\/"),
        player=(HERE / "player.js").read_text(),
    ))
    return path
