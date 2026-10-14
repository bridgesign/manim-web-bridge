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
window.__bridgeChunk = (k, c) => {
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
      s.src = `steps/step_${String(k).padStart(3, "0")}.js`;
      s.onerror = () => reject(new Error("cannot load " + s.src));
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
// Videos (VideoMobject): muted so the browser lets them autoplay.
// A frame-sequence clip ({frames: [...], fps, loop}) behaves like a <video>:
// play/pause/currentTime, plus frame() for the image to draw now.
function framePlayer({ frames, seq, fps, loop }) {
  const imgs = frames.map((src) => { const im = new Image(); im.onload = () => draw(); im.src = src; return im; });
  let t0 = 0, at = 0, playing = false;
  const clock = () => performance.now() / 1000;
  return {
    get readyState() { return imgs[0].complete ? 4 : 0; },
    get paused() { return !playing; },
    get currentTime() { return playing ? clock() - t0 : at; },
    set currentTime(v) { at = v; t0 = clock() - v; },
    play() { t0 = clock() - at; playing = true; return Promise.resolve(); },
    pause() { at = this.currentTime; playing = false; },
    frame() {
      const order = seq || imgs.map((_, k) => k), i = Math.floor(this.currentTime * fps);
      return imgs[order[loop ? i % order.length : Math.min(i, order.length - 1)]];
    },
  };
}

const videoCache = [];
function videoEl(i) {
  if (!videoCache[i]) videoCache[i] = makeClip(DATA.videos[i]);
  return videoCache[i];
}
function makeClip(entry) {
  if (entry.frames) return framePlayer(entry);
  const { src, loop } = typeof entry === "string" ? { src: entry, loop: false } : entry;
  const v = document.createElement("video");
  Object.assign(v, { src, loop, muted: true, playsInline: true, preload: "auto" });
  v.addEventListener("loadeddata", () => draw());
  return v;
}

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
  if (look.img !== undefined || look.vid !== undefined) {
    const v = look.vid !== undefined ? videoEl(look.vid) : null;
    const im = v ? (v.frame ? v.frame() : v) : imageEl(look.img);
    const ready = v ? v.readyState >= 2 && (!v.frame || im.complete) : im.complete;
    if (!ready || look.o <= 0) return;
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
  const visibleVideos = new Set();
  for (const u of order) {
    const l = looks.get(u);
    if (!l) continue;
    paint(l);
    if (l.vid !== undefined && l.o > 0) visibleVideos.add(l.vid);
  }
  syncVideos(visibleVideos);
}

// A video starts from the beginning when it comes on screen, pauses when it
// leaves, and keeps the canvas redrawing while it plays.
let liveVideos = new Set(), videoLoop = false;
function syncVideos(visible) {
  for (const i of visible) if (!liveVideos.has(i)) { videoEl(i).currentTime = 0; videoEl(i).play().catch(() => {}); }
  for (const i of liveVideos) if (!visible.has(i)) videoEl(i).pause();
  liveVideos = visible;
  if (liveVideos.size && !videoLoop) {
    videoLoop = true;
    const tick = () => { if (!liveVideos.size) { videoLoop = false; return; } draw(); requestAnimationFrame(tick); };
    requestAnimationFrame(tick);
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

async function play(cmd) {
  const tracks = cmd.tr.map((tr) => ({ tr, A: lookOf(tr.a), B: lookOf(tr.b) }));
  const t0 = now();
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

async function frames(cmd) {
  const t0 = now();
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

async function wait(cmd) {
  const t0 = now();
  while (!cancel && now() - t0 < cmd.d) await frame();
}

// ---------------------------------------------------------------------------
// Steps and navigation
// ---------------------------------------------------------------------------
let current = -1, running = null;

async function jump(i) {
  const k = Math.max(0, Math.min(N_STEPS - 1, i));
  await ensureStep(k);
  current = k;
  setDisplay(DATA.steps[current].end);
  draw();
  history.replaceState(null, "", "#" + current);
}

async function runStep(i) {
  await ensureStep(i);
  current = i;
  history.replaceState(null, "", "#" + i);
  cancel = false;
  for (const cmd of DATA.steps[i].cmds) {
    if (cmd.op === "set") { setDisplay(cmd.d); draw(); }
    else if (cmd.op === "play") await play(cmd);
    else if (cmd.op === "frames") await frames(cmd);
    else if (cmd.op === "wait") await wait(cmd);
  }
  await jump(i);                                     // land exactly on the recorded end state
  if (i + 1 < N_STEPS) ensureStep(i + 1).catch(() => {});   // prefetch while the speaker talks
  window.__bridgeIdle = true;
}

function start(i) {
  window.__bridgeIdle = false;
  running = runStep(i).finally(() => { running = null; });
}

async function next() {
  if (running) { cancel = true; await running; return; }   // first click finishes the step
  if (current + 1 < N_STEPS) start(current + 1);
}

async function prev() {
  if (running) { cancel = true; await running; }
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
window.bridge = { next, prev, jump, steps: N_STEPS, get current() { return current; }, get _videos() { return videoCache; } };

resize();
const fromHash = parseInt(location.hash.slice(1), 10);
if (Number.isInteger(fromHash)) { await jump(fromHash); window.__bridgeIdle = true; }
else start(0);
