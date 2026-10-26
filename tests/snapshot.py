"""
Headless check of an exported page: plays every step, saves a screenshot of
each step's end state, and reports browser console errors.

    python tests/snapshot.py page.html out_dir [--width 1280]
    python tests/snapshot.py page.html out_dir --steps 0,7   # only these steps (end states, no playback)
    python tests/snapshot.py page.html out_dir --film 3      # frames while step 3 plays
"""
import argparse
import asyncio
from pathlib import Path

from playwright.async_api import async_playwright


async def main(page_path, out: Path, width: int, only=None, film=None, every=0.25, count=12):
    if str(page_path).startswith(("http://", "https://")):
        url = str(page_path)               # a served deck (manim_web_bridge.serve)
    else:
        page_path = Path(page_path)
        if page_path.is_dir():             # a deck folder: open its main.html
            page_path = page_path / "main.html"
        url = page_path.resolve().as_uri()
    out.mkdir(parents=True, exist_ok=True)
    errors = []
    async with async_playwright() as p:
        browser = await p.chromium.launch(args=["--use-gl=angle", "--use-angle=swiftshader", "--enable-unsafe-swiftshader"])
        page = await browser.new_page(viewport={"width": width, "height": round(width * 9 / 16)})
        page.on("console", lambda m: m.type in ("error", "warning") and errors.append(f"{m.type}: {m.text}"))
        page.on("pageerror", lambda e: errors.append(f"pageerror: {e}"))
        if only is not None or film is not None:
            # a hash opens a place without playing anything ("#0" is the start; other
            # numbers may name slides, so steps are reached with bridge.jump)
            # film 0: load without a hash (step 0 autoplays); film n: jump to the
            # end of step n-1, then play step n
            await page.goto(url + ("" if film == 0 else "#0"))
            await page.wait_for_function("window.bridge && window.bridge.current >= 0", timeout=60000)
            if film:
                await page.evaluate(f"window.bridge.jump({film - 1})")
                await page.wait_for_timeout(300)
                await page.evaluate("window.bridge.next()")
            if film is not None:
                for k in range(count):
                    await page.wait_for_timeout(every * 1000)
                    await page.screenshot(path=str(out / f"film_{film:03d}_{k:02d}.png"))
            for i in only or []:
                await page.evaluate(f"window.bridge.jump({i})")
                await page.wait_for_timeout(200)
                await page.screenshot(path=str(out / f"step_{i:03d}.png"))
            await browser.close()
            print(f"saved -> {out}")
            for e in dict.fromkeys(errors):
                print(e[:300])
            return
        await page.goto(url)
        await page.wait_for_function("window.bridge && window.__bridgeIdle === true", timeout=60000)
        steps = await page.evaluate("window.bridge.steps")
        for i in range(steps):
            if i > 0:   # each step once, in order (loops: one pass; no "auto" chaining)
                await page.evaluate(f"window.bridge.run({i})")
            await page.screenshot(path=str(out / f"step_{i:03d}.png"))
        await browser.close()
    print(f"{steps} steps -> {out}")
    for e in dict.fromkeys(errors):
        print(e[:300])


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("page", help="a deck folder, its main.html, or an http:// URL of a served deck")
    ap.add_argument("out", type=Path)
    ap.add_argument("--width", type=int, default=1280)
    ap.add_argument("--steps", help="comma-separated step indices to capture (end states)")
    ap.add_argument("--film", type=int, help="capture frames while this step plays")
    ap.add_argument("--every", type=float, default=0.25, help="seconds between film frames")
    ap.add_argument("--count", type=int, default=12, help="number of film frames")
    a = ap.parse_args()
    only = [int(x) for x in a.steps.split(",")] if a.steps else None
    asyncio.run(main(a.page, a.out, a.width, only, a.film, a.every, a.count))
