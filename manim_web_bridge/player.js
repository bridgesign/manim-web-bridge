// manim-web-bridge player: replays steps recorded in Python (recorder.py) on
// an HTML canvas. No dependencies.
//
// Drawing is a port of manim's Cairo camera (manim/camera/camera.py):
//   path  = Camera.set_cairo_context_path   (4 control points per cubic,
//           new subpath where a curve doesn't start at the previous end,
//           close a subpath that ends where it started)
//   paint = Camera.display_vectorized       (fill, then stroke; nonzero
//           winding rule; stroke width = width * 0.01 frame units)
// Canvas 2D and Cairo share this model, so pictures match manim's.
//
//   next: ArrowRight, Space, PageDown, Enter, click   (finishes a running step first)
//   prev: ArrowLeft, PageUp, Backspace                (jumps to the previous step's end)
//   Home / End, F = fullscreen. The URL hash (#12) shows step 12 directly.

// Two page forms (see export.py):
//  * folder (main.html): a small header (#bridge-head) + one script per step,
//    steps/step_NNN.js, loaded right before the step is shown;
//  * single file: everything inline in #bridge-data (gzip + base64, or JSON).
const HEAD = document.getElementById("bridge-head");
async function loadData() {
  if (HEAD) {
    const h = JSON.parse(HEAD.textContent);
    return { ...h, states: [], geoms: [], steps: new Array(h.deps.length) };
  }
  const el = document.getElementById("bridge-data");
  if (el.type === "application/json") return JSON.parse(el.textContent);
  const bytes = Uint8Array.from(atob(el.textContent.trim()), (c) => c.charCodeAt(0));
  const stream = new Blob([bytes]).stream().pipeThrough(new DecompressionStream("gzip"));
  return JSON.parse(await new Response(stream).text());
}
const DATA = await loadData();
const N_STEPS = DATA.steps.length;

// Chunk k (folder form) calls __bridgeChunk(k, {step, states, geoms}).
const chunkWaiters = new Map(), chunkLoads = new Map();
// A rebuild while the page is open replaces main.html and every step file;
// a step from another build (or a missing one) means the page is stale, so
// reload it once, at the same step (the URL hash), instead of mixing builds.
function reloadStale() {
  const key = "bridge-reloaded-" + DATA.build;
  try { if (sessionStorage.getItem(key)) return; sessionStorage.setItem(key, "1"); } catch (e) {}
  location.reload();
}
window.__bridgeChunk = (k, c) => {
  if (DATA.build && c.build && c.build !== DATA.build) { reloadStale(); return; }
  for (const i in c.states) DATA.states[i] = c.states[i];
  for (const i in c.geoms) DATA.geoms[i] = c.geoms[i];
  DATA.steps[k] = c.step;
  chunkWaiters.get(k)?.();
};
function loadChunk(k) {
  if (!HEAD || DATA.steps[k]) return Promise.resolve();
  if (!chunkLoads.has(k)) {
    chunkLoads.set(k, new Promise((resolve, reject) => {
      chunkWaiters.set(k, resolve);
      const s = document.createElement("script");      // <script>, not fetch(): works from file://
      s.src = `steps/step_${String(k).padStart(3, "0")}.js` + (DATA.build ? `?b=${DATA.build}` : "");
      s.onerror = () => { reloadStale(); reject(new Error("cannot load " + s.src)); };
      document.head.appendChild(s);
    }));
  }
  return chunkLoads.get(k);
}
// Everything step k needs: its own chunk + the chunks that introduced what it shows.
const ensureStep = (k) => (HEAD ? Promise.all(DATA.deps[k].map(loadChunk)) : Promise.resolve());
const [FW, FH] = DATA.frame;
const LINE_WIDTH_MULTIPLE = 0.01;          // manim Camera.cairo_line_width_multiple

// ---------------------------------------------------------------------------
// Canvas, sized to the window with device-pixel-ratio sharpness
// ---------------------------------------------------------------------------
const canvas = document.getElementById("stage");
const ctx = canvas.getContext("2d");

function resize() {
  const r = canvas.getBoundingClientRect(), dpr = window.devicePixelRatio || 1;
  canvas.width = Math.round(r.width * dpr);
  canvas.height = Math.round(r.height * dpr);
  draw();
}
window.addEventListener("resize", resize);

// ---------------------------------------------------------------------------
// Shapes: a "look" is {pts: Float64Array of x,y,..., f, fo, c, co, w} or an image
// ---------------------------------------------------------------------------
// Images and clips are created on first use (in the folder form their files
// are only fetched then).
const imageCache = [];
function imageEl(i) {
  if (!imageCache[i]) { const im = new Image(); im.onload = () => draw(); im.src = DATA.images[i]; imageCache[i] = im; }
  return imageCache[i];
}
// Clips (VideoMobject): {name, kind: "image" | "video", loop, url}. Every clip
// is a real element over the canvas at its rectangle (see syncOverlays): an
// <img> for an animated image (browsers only animate images they show), a
// muted <video> for a video, both as the file is (an MP4 stays an MP4). A
// fresh element each time the clip comes on screen restarts it. A clip whose
// file is not there (missing at build time and not yet dropped into media/)
// shows as a dashed placeholder, and is tried again next time it appears.
const CLIPS = (DATA.videos || []).map((v) =>
  v.kind ? { ...v, url: v.url || "media/" + v.name }
  : v.anim !== undefined ? { name: "clip", kind: "image", url: v.anim, loop: !!v.loop }      // older exports
  : { name: "clip", kind: "video", url: v.src || v, loop: !!v.loop });

const rgb = (hex) => [1, 3, 5].map((i) => parseInt(hex.slice(i, i + 2), 16));
const css = (c, a) => `rgba(${c[0]},${c[1]},${c[2]},${a})`;
const same = (p, i, j) => Math.abs(p[i] - p[j]) < 1e-6 && Math.abs(p[i + 1] - p[j + 1]) < 1e-6;

function lookOf(s) {
  const st = DATA.states[s];
  if (st.img !== undefined || st.vid !== undefined) return { ...st };
  const g = DATA.geoms[st.g], pts = new Float64Array(g.length);
  for (let i = 0; i < g.length; i += 2) { pts[i] = g[i] / 1000 + st.x; pts[i + 1] = g[i + 1] / 1000 + st.y; }
  return { pts, f: rgb(st.f), fo: st.fo, c: rgb(st.c), co: st.co, w: st.w };
}

function tracePath(p) {
  ctx.beginPath();
  let start = 0;
  for (let k = 0; k < p.length; k += 8) {             // one cubic = 4 points = 8 numbers
    if (k === 0 || !same(p, k, k - 2)) {             // a new subpath begins here
      if (k > 0 && same(p, start, k - 2)) ctx.closePath();
      ctx.moveTo(p[k], p[k + 1]);
      start = k;
    }
    ctx.bezierCurveTo(p[k + 2], p[k + 3], p[k + 4], p[k + 5], p[k + 6], p[k + 7]);
  }
  if (p.length && same(p, start, p.length - 2)) ctx.closePath();
}

function paint(look) {
  if (look.vid !== undefined) return;                  // clips are elements over the canvas
  if (look.img !== undefined) {
    const im = imageEl(look.img);
    if (!im.complete || look.o <= 0) return;
    ctx.save();
    ctx.globalAlpha = look.o;
    ctx.translate(look.x, look.y);
    ctx.scale(1, -1);                                  // images are y-down
    ctx.drawImage(im, -look.iw / 2, -look.ih / 2, look.iw, look.ih);
    ctx.restore();
    return;
  }
  if (!look.pts.length) return;
  tracePath(look.pts);
  if (look.fo > 0) { ctx.fillStyle = css(look.f, look.fo); ctx.fill(); }
  if (look.w > 0 && look.co > 0) {
    ctx.strokeStyle = css(look.c, look.co);
    ctx.lineWidth = look.w * LINE_WIDTH_MULTIPLE;
    ctx.stroke();
  }
}

// ---------------------------------------------------------------------------
// Screen: ordered list of uids, each with its current look
// ---------------------------------------------------------------------------
let order = [];
const looks = new Map();                 // uid -> look
const shownState = new Map();            // uid -> state index (skips rebuilding unchanged looks)

function draw() {
  const s = canvas.width / FW;
  ctx.setTransform(1, 0, 0, 1, 0, 0);
  ctx.fillStyle = DATA.background;
  ctx.fillRect(0, 0, canvas.width, canvas.height);
  ctx.setTransform(s, 0, 0, -s, canvas.width / 2, canvas.height / 2);   // manim frame -> pixels
  ctx.lineJoin = "miter"; ctx.lineCap = "butt"; ctx.miterLimit = 10;   // Cairo defaults
  const overlays = [];
  for (const u of order) {
    const l = looks.get(u);
    if (!l) continue;
    paint(l);
    if (l.vid !== undefined && l.o > 0) overlays.push(l);
  }
  syncOverlays(overlays);
}

// One element per visible clip, kept over its rectangle.
const overlayEls = new Map();            // vid -> container <div>

function makeOverlay(c) {
  const box = document.createElement("div");
  Object.assign(box.style, { position: "fixed", pointerEvents: "none", overflow: "hidden", boxSizing: "border-box" });
  const media = document.createElement(c.kind === "video" ? "video" : "img");
  if (c.kind === "video") {
    Object.assign(media, { muted: true, autoplay: true, playsInline: true, loop: c.loop });
    media.setAttribute("muted", "");                   // autoplay needs the attribute in some browsers
  }
  Object.assign(media.style, { width: "100%", height: "100%", objectFit: "fill", display: "block" });
  media.addEventListener("error", () => {             // not there (yet): a placeholder, not an error
    media.remove();
    Object.assign(box.style, { border: "2px dashed #9CA3AF", display: "flex", alignItems: "center",
                               justifyContent: "center", color: "#6B7280", font: "14px sans-serif" });
    box.textContent = `${c.url}: not there yet, or a format this browser cannot play`;
  });
  media.src = c.url;
  box.appendChild(media);
  return box;
}

function syncOverlays(list) {
  const r = canvas.getBoundingClientRect(), k = r.width / FW;
  const want = new Set(list.map((l) => l.vid));
  for (const [vid, el] of overlayEls) if (!want.has(vid)) { el.remove(); overlayEls.delete(vid); }
  for (const l of list) {
    let el = overlayEls.get(l.vid);
    if (!el) {                                         // new on screen: a fresh element restarts the clip
      el = makeOverlay(CLIPS[l.vid]);
      document.body.appendChild(el);
      overlayEls.set(l.vid, el);
    }
    Object.assign(el.style, {
      left: `${r.left + (l.x - l.iw / 2 + FW / 2) * k}px`, top: `${r.top + (FH / 2 - l.y - l.ih / 2) * k}px`,
      width: `${l.iw * k}px`, height: `${l.ih * k}px`, opacity: String(l.o),
    });
  }
}

function setLook(u, s) {
  if (shownState.get(u) !== s) { looks.set(u, lookOf(s)); shownState.set(u, s); }
}

function setDisplay(list) {
  order = list.map(([u]) => u);
  const keep = new Set(order);
  for (const u of [...looks.keys()]) if (!keep.has(u)) { looks.delete(u); shownState.delete(u); }
  for (const [u, s] of list) setLook(u, s);
}

// ---------------------------------------------------------------------------
// Ports of manim's interpolation helpers
// ---------------------------------------------------------------------------
const lerp = (a, b, t) => a + (b - a) * t;

function lerpLook(A, B, t) {                         // Mobject.interpolate: points + style
  if (A.img !== undefined || A.vid !== undefined) {
    return { ...B, x: lerp(A.x, B.x, t), y: lerp(A.y, B.y, t), iw: lerp(A.iw, B.iw, t), ih: lerp(A.ih, B.ih, t), o: lerp(A.o, B.o, t) };
  }
  const n = Math.min(A.pts.length, B.pts.length), pts = new Float64Array(n);
  for (let i = 0; i < n; i++) pts[i] = lerp(A.pts[i], B.pts[i], t);
  const mix = (p, q) => p.map((v, i) => Math.round(lerp(v, q[i], t)));
  return { pts, f: mix(A.f, B.f), fo: lerp(A.fo, B.fo, t), c: mix(A.c, B.c), co: lerp(A.co, B.co, t), w: lerp(A.w, B.w, t) };
}

function partialCubic(p, k, b) {                     // partial_bezier_points(curve, 0, b): de Casteljau
  const P = [0, 1, 2, 3].map((i) => [p[k + 2 * i], p[k + 2 * i + 1]]);
  const L = (u, v) => [lerp(u[0], v[0], b), lerp(u[1], v[1], b)];
  const a1 = L(P[0], P[1]), b1 = L(P[1], P[2]), c1 = L(P[2], P[3]);
  const a2 = L(a1, b1), b2 = L(b1, c1);
  return [P[0], a1, a2, L(a2, b2)].flat();
}

function partialLook(look, alpha) {                  // VMobject.pointwise_become_partial(m, 0, alpha)
  if (alpha >= 1) return look;
  const nCurves = look.pts.length / 8;
  if (alpha <= 0 || nCurves === 0) return { ...look, pts: new Float64Array(0) };
  const idx = Math.floor(alpha * nCurves), res = (nCurves * alpha) % 1;   // integer_interpolate
  const out = Array.from(look.pts.subarray(0, idx * 8));
  if (idx < nCurves) out.push(...partialCubic(look.pts, idx * 8, res));
  return { ...look, pts: Float64Array.from(out) };
}

// manim/utils/rate_functions.py
const sigmoid = (x) => 1 / (1 + Math.exp(-x));
const clamp01 = (f) => (t) => (t <= 0 ? 0 : t >= 1 ? 1 : f(t));
const smooth = (t, k = 10) => { const e = sigmoid(-k / 2); return Math.min(Math.max((sigmoid(k * (t - 0.5)) - e) / (1 - 2 * e), 0), 1); };
const thereAndBack = (t) => smooth(t < 0.5 ? 2 * t : 2 * (1 - t));
const RATE = {
  linear: clamp01((t) => t),
  smooth: clamp01((t) => smooth(t)),
  rush_into: clamp01((t) => 2 * smooth(t / 2)),
  rush_from: clamp01((t) => 2 * smooth(t / 2 + 0.5) - 1),
  slow_into: clamp01((t) => Math.sqrt(1 - (1 - t) * (1 - t))),
  double_smooth: clamp01((t) => (t < 0.5 ? 0.5 * smooth(2 * t) : 0.5 * (1 + smooth(2 * t - 1)))),
  there_and_back: (t) => (t <= 0 || t >= 1 ? 0 : thereAndBack(t)),
  there_and_back_with_pause: (t) => {
    if (t <= 0 || t >= 1) return 0;
    const r = 1 / 3, a = 2 / (1 - r);
    return t < 0.5 - r / 2 ? smooth(a * t) : t < 0.5 + r / 2 ? 1 : smooth(a - a * t);
  },
  running_start: clamp01((t) => {
    const m = 1 - t, p = -0.5;
    return 15 * t ** 2 * m ** 4 * p + 20 * t ** 3 * m ** 3 * p + 15 * t ** 4 * m ** 2 + 6 * t ** 5 * m + t ** 6;
  }),
  wiggle: (t) => (t <= 0 || t >= 1 ? 0 : thereAndBack(t) * Math.sin(2 * Math.PI * t)),
  lingering: clamp01((t) => Math.min(1, t / 0.8)),
  exponential_decay: clamp01((t) => 1 - Math.exp(-t / 0.1)),
};

// Look of one track at local progress x in [0, 1] (rate function applied here).
function trackLook(tr, A, B, x) {
  const a = (RATE[tr.r] || RATE.smooth)(x);
  if (tr.k === "tween") return lerpLook(A, B, a);
  if (tr.k === "create") return partialLook(B, a);
  // write = DrawBorderThenFill.interpolate_submobject: outline first, then fill
  if (a >= 1) return B;
  return a < 0.5 ? partialLook(A, 2 * a) : lerpLook(A, B, 2 * a - 1);
}

// ---------------------------------------------------------------------------
// Commands
// ---------------------------------------------------------------------------
let cancel = false;                                  // set when the user skips ahead
const now = () => performance.now() / 1000;
const frame = () => new Promise((r) => requestAnimationFrame(r));

// Each command gets its scheduled start time t0 (the step clock, see runStep):
// one that starts late catches up instead of pushing every later one back.
async function play(cmd, t0 = now()) {
  const tracks = cmd.tr.map((tr) => ({ tr, A: lookOf(tr.a), B: lookOf(tr.b) }));
  for (;;) {
    const t = cancel ? Infinity : now() - t0;
    for (const { tr, A, B } of tracks) {
      const x = tr.d > 0 ? (t - tr.t) / tr.d : t >= tr.t ? 1 : 0;
      looks.set(tr.u, trackLook(tr, A, B, Math.max(0, Math.min(1, x))));
      shownState.delete(tr.u);                       // look no longer matches a stored state
    }
    draw();
    if (t >= cmd.T) break;
    await frame();
  }
}

async function frames(cmd, t0 = now()) {
  let shown = -1;
  for (;;) {
    const i = cancel ? cmd.f.length - 1 : Math.min(cmd.f.length - 1, Math.floor((now() - t0) * cmd.fps));
    for (let k = shown + 1; k <= i; k++) {
      const f = cmd.f[k];
      for (const [u, s] of f.u) { setLook(u, s); if (!order.includes(u)) order.push(u); }
      if (f.o) {
        order = f.o;
        for (const u of [...looks.keys()]) if (!order.includes(u)) { looks.delete(u); shownState.delete(u); }
      }
    }
    shown = i;
    draw();
    if (i >= cmd.f.length - 1) break;
    await frame();
  }
}

async function wait(cmd, t0 = now()) {
  while (!cancel && now() - t0 < cmd.d) await frame();
}

// ---------------------------------------------------------------------------
// Preloading: while a step plays, the next one is fetched in the background
// ---------------------------------------------------------------------------
// preload(k) loads step k's chunks, then fetches and decodes every image and
// clip it shows, so nothing pops in late when the click comes. It runs when
// the browser is idle and never blocks playback; an "auto" step pulls in the
// step it flows into as well.
const preloaded = new Set(), heldClips = [];
const idle = () => new Promise((r) => (window.requestIdleCallback ? requestIdleCallback(() => r(), { timeout: 500 })
                                                                 : setTimeout(r, 50)));

function statesOf(step) {
  const ids = new Set(step.end.map(([, s]) => s));
  for (const cmd of step.cmds) {
    if (cmd.op === "set") cmd.d.forEach(([, s]) => ids.add(s));
    else if (cmd.op === "play") cmd.tr.forEach((tr) => { ids.add(tr.a); ids.add(tr.b); });
    else if (cmd.op === "frames") cmd.f.forEach((f) => f.u.forEach(([, s]) => ids.add(s)));
  }
  return ids;
}

async function warmMedia(step) {
  const jobs = [];
  for (const s of statesOf(step)) {
    const st = DATA.states[s];
    if (!st) continue;
    if (st.img !== undefined) jobs.push(imageEl(st.img).decode?.().catch(() => {}));
    else if (st.vid !== undefined) {
      const c = CLIPS[st.vid];
      if (heldClips[st.vid]) continue;
      if (c.kind === "image") {
        const im = new Image();                         // same URL as the overlay <img> -> cached
        im.src = c.url;
        heldClips[st.vid] = im;                         // keep it alive so the cache entry stays
        jobs.push(im.decode?.().catch(() => {}));       // a missing file is simply skipped
      } else {
        const v = document.createElement("video");     // fetch ahead; the overlay plays its own
        Object.assign(v, { preload: "auto", muted: true, src: c.url });
        heldClips[st.vid] = v;
      }
    }
  }
  await Promise.all(jobs);
}

async function preload(k) {
  for (; k < N_STEPS && !preloaded.has(k); k++) {
    preloaded.add(k);
    await idle();
    try {
      await ensureStep(k);
      await warmMedia(DATA.steps[k]);
    } catch (e) {
      preloaded.delete(k);                              // try again on the next chance
      return;
    }
    if (!DATA.steps[k].auto) return;                    // an "auto" step: its follower too
  }
}

// ---------------------------------------------------------------------------
// Steps and navigation
// ---------------------------------------------------------------------------
let current = -1, running = null;

async function jump(i) {
  const k = Math.max(0, Math.min(N_STEPS - 1, i));
  history.replaceState(null, "", "#" + k);           // first, so a stale-page reload lands here
  await ensureStep(k);
  current = k;
  setDisplay(DATA.steps[current].end);
  draw();
  history.replaceState(null, "", "#" + current);
  preload(current + 1);
}

async function runStep(i, once = false) {
  history.replaceState(null, "", "#" + i);           // first, so a stale-page reload lands here
  await ensureStep(i);
  preload(i + 1);                                    // background, while this step plays
  current = i;
  history.replaceState(null, "", "#" + i);
  cancel = false;
  const step = DATA.steps[i];
  do {                                               // a loop step repeats until a click
    // step clock: command k starts at (pass start + durations of 0..k-1), so the
    // frame-rounding of each command never adds up (clips stay in sync over a pass)
    let due = now();
    for (const cmd of step.cmds) {
      if (cmd.op === "set") { setDisplay(cmd.d); draw(); continue; }
      if (cmd.op === "play") await play(cmd, due);
      else if (cmd.op === "frames") await frames(cmd, due);
      else if (cmd.op === "wait") await wait(cmd, due);
      due += cmd.op === "play" ? cmd.T : cmd.op === "wait" ? cmd.d : cmd.f.length / cmd.fps;
      if (now() - due > 0.25) due = now();           // far behind (tab was hidden): restart the clock
    }
    if (step.loop) await frame();                    // never spin without yielding
  } while (step.loop && !cancel && !once);
  await jump(i);                                     // land exactly on the recorded end state
  window.__bridgeIdle = true;
}

let chain = true;                                    // false: stop following "auto" steps

function start(i) {
  window.__bridgeIdle = false;
  chain = true;
  running = (async () => {
    for (let k = i; ; k++) {
      await runStep(k);
      if (!DATA.steps[k].auto || !chain || k + 1 >= N_STEPS) break;   // "auto": no click needed
      window.__bridgeIdle = false;
    }
  })().finally(() => { running = null; });
}

async function next() {
  if (running) {                                     // first click finishes the step ...
    cancel = true;
    if (!DATA.steps[current].loop) return;           // (an "auto" chain then carries on)
    chain = false; await running;                    // ... a looping one moves straight on
  }
  if (current + 1 < N_STEPS) start(current + 1);
}

async function prev() {
  if (running) { chain = false; cancel = true; await running; }
  await jump(current - 1);
}

document.addEventListener("keydown", (e) => {
  if (["ArrowRight", " ", "PageDown", "Enter"].includes(e.key)) { e.preventDefault(); next(); }
  else if (["ArrowLeft", "PageUp", "Backspace"].includes(e.key)) { e.preventDefault(); prev(); }
  else if (e.key === "Home") jump(0);
  else if (e.key === "End") jump(N_STEPS - 1);
  else if (e.key === "f") document.fullscreenElement ? document.exitFullscreen() : document.documentElement.requestFullscreen();
});
canvas.addEventListener("click", next);

// for tests / automation
window.bridge = { next, prev, jump, preload, run: (i) => runStep(i, true), steps: N_STEPS, get current() { return current; },
                  get _overlays() { return [...overlayEls.values()]; } };

resize();
const fromHash = parseInt(location.hash.slice(1), 10);
if (Number.isInteger(fromHash)) { await jump(fromHash); window.__bridgeIdle = true; }
else start(0);
