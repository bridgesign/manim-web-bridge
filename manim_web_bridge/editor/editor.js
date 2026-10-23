// manim-web-bridge editor: edit a slide's Python code from the page.
// Loaded only on pages served by manim_web_bridge.serve (after player.js).
//
//   E            edit mode on / off (a click then selects instead of advancing)
//   click        select the object under the pointer: its name, where it was made
//   right-click  add an object there (text, equation, box, circle, arrow, image)
//   drag         select everything in a rectangle -> group it (VGroup)
//   "this item"  the statement that made it (in the slide, or in a helper)
//   "the slide"  the whole slide function
//   Ctrl/Cmd+S   save (only if it parses); Esc closes
//
// Added code goes into the slide function's sections (created if missing):
//   ## Objects       what is made (positions and sizes as canvas fractions)
//   ## Groups        VGroup(...) of selected objects
//   ## Transitions   how each object comes on screen
//
// Saving writes the file; the server notices, records that slide again and
// the page reloads it in place. Code is checked as you type (/api/lint:
// syntax errors, plus pyflakes' warnings when it is installed).
// Every size here is a fraction of the canvas height, like the slides.

const bridge = window.bridge;
const canvas = bridge.canvas;
const unit = () => canvas.getBoundingClientRect().height;          // the canvas height, in px
const px = (f) => `${f * unit()}px`;

// -- styles (sizes filled in by layout(), from the canvas height) --------------
const css = document.createElement("style");
css.textContent = `
.bw-pill { position: fixed; z-index: 20; border: none; cursor: pointer; color: #fff; background: #182B49;
           font-family: sans-serif; opacity: 0.85; }
.bw-pill.on { background: #C69214; opacity: 1; }
.bw-panel { position: fixed; z-index: 20; background: #fff; color: #1F1F1F; font-family: sans-serif;
            box-shadow: 0 0.3em 1.2em rgba(0,0,0,0.25); display: none; }
.bw-panel h3 { margin: 0 0 0.4em; font-size: 1.1em; color: #182B49; word-break: break-word; }
.bw-panel .where { font-family: ui-monospace, monospace; font-size: 0.85em; color: #6B7280; margin: 0.15em 0; }
.bw-panel .where a { color: #6B7280; }
.bw-panel button, .bw-modal button { font: inherit; cursor: pointer; border: 1px solid #182B49; background: #fff;
            color: #182B49; border-radius: 0.3em; padding: 0.25em 0.7em; margin: 0.4em 0.4em 0 0; }
.bw-panel button.primary, .bw-modal button.primary { background: #182B49; color: #fff; }
.bw-shade { position: fixed; inset: 0; z-index: 30; background: rgba(0,0,0,0.35); display: none; }
.bw-modal { position: fixed; z-index: 31; background: #fff; display: none; flex-direction: column;
            font-family: sans-serif; box-shadow: 0 0.5em 2em rgba(0,0,0,0.35); }
.bw-modal .head { display: flex; justify-content: space-between; align-items: baseline; color: #182B49; }
.bw-modal .head .file { font-family: ui-monospace, monospace; color: #6B7280; font-size: 0.85em; }
.bw-modal .CodeMirror { flex: 1; border: 1px solid #D1D5DB; height: auto; }
.bw-modal .status { color: #6B7280; font-size: 0.85em; min-height: 1.3em; }
.bw-modal .status.bad { color: #B42318; }
.bw-menu { position: fixed; z-index: 25; background: #fff; display: none; font-family: sans-serif;
           box-shadow: 0 0.3em 1.2em rgba(0,0,0,0.25); }
.bw-menu div { cursor: pointer; } .bw-menu div:hover { background: #F3F4F6; }
.bw-menu .title { color: #6B7280; cursor: default; } .bw-menu .title:hover { background: none; }
.bw-rubber { position: fixed; z-index: 15; border-style: dashed; border-color: #C69214;
             background: rgba(198, 146, 20, 0.08); pointer-events: none; display: none; }
`;
document.head.appendChild(css);

// -- elements --------------------------------------------------------------------
const pill = Object.assign(document.createElement("button"), { className: "bw-pill", textContent: "edit (E)" });
const panel = Object.assign(document.createElement("div"), { className: "bw-panel" });
const shade = Object.assign(document.createElement("div"), { className: "bw-shade" });
const modal = Object.assign(document.createElement("div"), { className: "bw-modal" });
modal.innerHTML = `<div class="head"><b class="title"></b><span class="file"></span></div>
  <textarea></textarea><div class="status"></div>
  <div><button class="primary save">Save (Ctrl+S)</button><button class="cancel">Cancel (Esc)</button></div>`;
const menu = Object.assign(document.createElement("div"), { className: "bw-menu" });
const rubber = Object.assign(document.createElement("div"), { className: "bw-rubber" });
document.body.append(pill, panel, shade, modal, menu, rubber);

function layout() {                                   // everything sized from the canvas
  const r = canvas.getBoundingClientRect(), h = r.height;
  Object.assign(pill.style, { right: `${innerWidth - r.right + 0.015 * h}px`, top: `${r.top + 0.015 * h}px`,
                              fontSize: px(0.022), padding: `${0.006 * h}px ${0.014 * h}px`, borderRadius: px(0.02) });
  Object.assign(panel.style, { fontSize: px(0.024), padding: px(0.018), borderRadius: px(0.01), maxWidth: px(0.75) });
  Object.assign(modal.style, { left: `${r.left + 0.04 * r.width}px`, top: `${r.top + 0.05 * h}px`,
                               width: `${0.92 * r.width}px`, height: `${0.9 * h}px`, fontSize: px(0.024),
                               padding: px(0.02), borderRadius: px(0.012), gap: px(0.01) });
  Object.assign(menu.style, { fontSize: px(0.024), borderRadius: px(0.01), padding: `${0.006 * h}px 0` });
  rubber.style.borderWidth = `${Math.max(1, 0.003 * h)}px`;
  if (editor) { editor.getWrapperElement().style.fontSize = px(0.024); editor.refresh(); }
}
addEventListener("resize", layout);

// -- edit mode and selection ----------------------------------------------------------
let info = null;
function setEditing(on) {
  bridge.editing = on;
  pill.classList.toggle("on", on);
  pill.textContent = on ? "editing: click an item (E to stop)" : "edit (E)";
  canvas.style.cursor = on ? "crosshair" : "";
  if (!on) { panel.style.display = "none"; bridge.highlight([]); }
}
pill.addEventListener("click", () => setEditing(!bridge.editing));
addEventListener("keydown", (e) => {
  if (modal.style.display === "flex") return;
  if (e.key === "e" || e.key === "E") { e.preventDefault(); setEditing(!bridge.editing); }
  if (e.key === "Escape" && bridge.editing) setEditing(false);
});

canvas.addEventListener("click", async (e) => {
  if (!bridge.editing || justDragged) { justDragged = false; return; }
  menu.style.display = "none";
  const uid = bridge.hitTest(e.clientX, e.clientY);
  if (uid === null) { panel.style.display = "none"; bridge.highlight([]); return; }
  info = await (await fetch(`/api/object/${uid}`)).json();
  if (info.error) return;
  bridge.highlight(info.group);
  showPanel(e.clientX, e.clientY);
});

const esc = (t) => String(t).replace(/[&<>"]/g, (c) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;" })[c]);
function showPanel(x, y) {
  const s = info.slide, here = info.sources;
  // "this item": the call in the slide's own file if there is one, else the innermost site
  const own = here.find(([f]) => f === s.file) || here[0];
  panel.innerHTML = `<h3>${esc(info.name)}</h3>`
    + (here.length ? here.map(([f, n]) => `<div class="where">made at <a href="vscode://file/${esc(info.root)}/${esc(f)}:${n}">${esc(f)}:${n}</a></div>`).join("")
                   : `<div class="where">made by the template or the bridge (not in this deck's code)</div>`)
    + `<div class="where">slide ${s.index + 1}: ${esc(s.function || "?")}() in ${esc(s.file || "?")}</div>`
    + (own ? `<button class="primary item">Edit this item</button>` : "")
    + (s.file ? `<button class="slide">Edit the slide</button>` : "")
    + `<button class="close">Close</button>`;
  panel.querySelector(".item")?.addEventListener("click", () => openEditor({ file: own[0], line: own[1] }, `${info.name}`));
  panel.querySelector(".slide")?.addEventListener("click", () => openEditor({ file: s.file, function: s.function }, `slide ${s.index + 1}: ${s.function}()`));
  panel.querySelector(".close").addEventListener("click", () => { panel.style.display = "none"; bridge.highlight([]); });
  panel.style.display = "block";
  const r = canvas.getBoundingClientRect(), w = panel.offsetWidth, h = panel.offsetHeight;
  panel.style.left = `${Math.min(x + 0.02 * r.height, r.right - w - 0.01 * r.height)}px`;
  panel.style.top = `${Math.min(y + 0.02 * r.height, r.bottom - h - 0.01 * r.height)}px`;
}

// -- the code popup (CodeMirror 5, Python, lint as you type) -----------------------------
let editor = null, target = null;
const status = modal.querySelector(".status");

function lintSource(text, done) {                        // CodeMirror's async lint hook
  if (!target) return done([]);
  fetch("/api/lint", { method: "POST", body: JSON.stringify({ ...target, code: text }) })
    .then((r) => r.json()).then(({ problems = [] }) => {
      done(problems.map((p) => ({
        from: CodeMirror.Pos(p.line - 1, p.col), to: CodeMirror.Pos(p.line - 1, p.col + 1),
        message: p.message, severity: p.severity,
      })));
      const errors = problems.filter((p) => p.severity === "error").length;
      status.className = "status" + (errors ? " bad" : "");
      status.textContent = errors ? `${errors} error(s): fix before saving`
        : problems.length ? `${problems.length} warning(s)` : "no problems found";
    }).catch(() => done([]));
}

async function openEditor(query, title) {
  const q = new URLSearchParams(query);
  const src = await (await fetch(`/api/source?${q}`)).json();
  if (src.error) { alert(src.error); return; }
  target = { file: src.file, start: src.start, end: src.end, sha: src.sha };
  modal.querySelector(".title").textContent = title;
  modal.querySelector(".file").textContent = `${src.file}, lines ${src.start}-${src.end}`;
  shade.style.display = "block"; modal.style.display = "flex";
  if (!editor) {
    editor = CodeMirror.fromTextArea(modal.querySelector("textarea"), {
      mode: "python", lineNumbers: true, indentUnit: 4, matchBrackets: true, gutters: ["CodeMirror-lint-markers"],
      lint: { getAnnotations: lintSource, async: true, delay: 400 },
      extraKeys: { "Ctrl-S": save, "Cmd-S": save, Esc: close, Tab: (cm) => cm.replaceSelection("    ") },
    });
  }
  editor.setOption("firstLineNumber", src.start);
  editor.setValue(src.code);
  editor.clearHistory();
  layout();
  editor.focus();
  status.className = "status"; status.textContent = "";
}

async function save() {
  if (!target) return;
  status.className = "status"; status.textContent = "saving ...";
  const r = await (await fetch("/api/source", { method: "POST",
    body: JSON.stringify({ ...target, code: editor.getValue() }) })).json();
  if (r.error) {
    status.className = "status bad";
    status.textContent = r.error + (r.problems ? ": " + r.problems.map((p) => `line ${p.line + target.start - 1}: ${p.message}`).join("; ") : "");
    return;
  }
  close();
  setEditing(false);                                  // the server records the slide again; the page follows
}
function close() { shade.style.display = "none"; modal.style.display = "none"; target = null; }
modal.querySelector(".save").addEventListener("click", save);
modal.querySelector(".cancel").addEventListener("click", close);
shade.addEventListener("click", close);


// -- right-click: add an object where you clicked -----------------------------------------
const KINDS = [["text", "Text", "the text"], ["equation", "Equation (LaTeX)", "the LaTeX"], ["box", "Box"],
               ["circle", "Circle"], ["arrow", "Arrow"], ["image", "Image", "the image file (path)"]];
canvas.addEventListener("contextmenu", (e) => {
  if (!bridge.editing || bridge.slide === null) return;
  e.preventDefault();
  const [fx, fy] = bridge.fraction(e.clientX, e.clientY);
  const pad = `${0.006 * unit()}px ${0.018 * unit()}px`;
  menu.innerHTML = `<div class="title" style="padding:${pad}">add at (${fx.toFixed(2)}, ${fy.toFixed(2)})</div>`
    + KINDS.map(([k, label]) => `<div data-kind="${k}" style="padding:${pad}">${label}</div>`).join("");
  menu.style.display = "block";
  menu.style.left = `${e.clientX}px`; menu.style.top = `${e.clientY}px`;
  menu.querySelectorAll("[data-kind]").forEach((item) => item.addEventListener("click", async () => {
    menu.style.display = "none";
    const [kind, , ask] = KINDS.find(([k]) => k === item.dataset.kind);
    const value = ask ? prompt(`New ${kind}: ${ask}`) : "";
    if (ask && !value) return;
    const r = await (await fetch("/api/insert", { method: "POST",
      body: JSON.stringify({ slide: bridge.slide, kind, fx, fy, value }) })).json();
    if (r.error) alert(r.error);                        // otherwise: the slide is recorded again, the page follows
  }));
});
addEventListener("click", (e) => { if (!menu.contains(e.target)) menu.style.display = "none"; }, true);

// -- drag: select a rectangle, group what is in it -------------------------------------------
let drag = null, justDragged = false;
canvas.addEventListener("mousedown", (e) => {
  if (!bridge.editing || e.button !== 0) return;
  drag = { x: e.clientX, y: e.clientY, moved: false };
});
addEventListener("mousemove", (e) => {
  if (!drag) return;
  const w = Math.abs(e.clientX - drag.x), h = Math.abs(e.clientY - drag.y);
  if (!drag.moved && Math.max(w, h) < 0.015 * unit()) return;   // a click, not a drag
  drag.moved = true;
  Object.assign(rubber.style, { display: "block", left: `${Math.min(e.clientX, drag.x)}px`, top: `${Math.min(e.clientY, drag.y)}px`,
                                width: `${w}px`, height: `${h}px` });
});
addEventListener("mouseup", async (e) => {
  if (!drag) return;
  const d = drag; drag = null; rubber.style.display = "none";
  if (!d.moved) return;
  justDragged = true;                                   // the click that follows is not a selection
  const uids = bridge.uidsIn(d.x, d.y, e.clientX, e.clientY);
  if (!uids.length) { bridge.highlight([]); panel.style.display = "none"; return; }
  bridge.highlight(uids);
  const g = await (await fetch("/api/group", { method: "POST", body: JSON.stringify({ uids }) })).json();
  if (g.error) return;
  panel.innerHTML = `<h3>${uids.length} parts selected</h3>`
    + `<div class="where">variables: ${g.names.length ? g.names.map(esc).join(", ") : "none"}</div>`
    + (g.unnamed.length ? `<div class="where">not in a variable of this slide: ${g.unnamed.slice(0, 4).map(esc).join("; ")}${g.unnamed.length > 4 ? " ..." : ""}</div>` : "")
    + (g.slides.length > 1 ? `<div class="where">(more than one slide)</div>` : "")
    + (g.names.length >= 2 ? `<button class="primary group">Group as ${g.type}(${g.names.map(esc).join(", ")})</button>` : "")
    + `<button class="close">Close</button>`;
  panel.querySelector(".group")?.addEventListener("click", async () => {
    const r = await (await fetch("/api/insert", { method: "POST",
      body: JSON.stringify({ slide: bridge.slide, kind: "group", names: g.names, type: g.type }) })).json();
    if (r.error) alert(r.error); else { panel.style.display = "none"; bridge.highlight([]); }
  });
  panel.querySelector(".close").addEventListener("click", () => { panel.style.display = "none"; bridge.highlight([]); });
  panel.style.display = "block";
  const r = canvas.getBoundingClientRect();
  panel.style.left = `${Math.min(e.clientX + 0.02 * r.height, r.right - panel.offsetWidth - 0.01 * r.height)}px`;
  panel.style.top = `${Math.min(e.clientY + 0.02 * r.height, r.bottom - panel.offsetHeight - 0.01 * r.height)}px`;
});

layout();
