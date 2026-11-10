// colorbar-hub: page logic. The analysis itself runs in worker.js (Python in WebAssembly).

const $ = (s) => document.querySelector(s);
const fmt = (v, d = 3) => Number(v).toFixed(d);
const esc = (s) => String(s).replace(/[&<>"]/g, (c) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;" }[c]));

const PDFJS_BASE = "https://cdn.jsdelivr.net/npm/pdfjs-dist@4.10.38/build/";

const S = { res: null, sel: 0, tab: "ext", file: null, pdfDoc: null, crops: new Map(), zoomUser: false, zoomVal: 4, seconds: 0 };

// ---- link to the source repo when hosted on GitHub Pages ------------------------------------
(() => {
  const m = location.hostname.match(/^([^.]+)\.github\.io$/);
  const repo = location.pathname.split("/").filter(Boolean)[0];
  if (m && repo) {
    const a = $("#repo");
    a.href = `https://github.com/${m[1]}/${repo}`;
    a.hidden = false;
  }
})();

// ---- worker -----------------------------------------------------------------------------------
let worker = null;
let engineState = "loading";
const pending = new Map();
let seq = 0;

function setEngine(text, cls = "") {
  const el = $("#engine");
  el.textContent = text;
  el.className = "engine " + cls;
}

function startWorker() {
  try {
    worker = new Worker("worker.js", { type: "module" });
  } catch (err) {
    engineState = "error";
    setEngine("This browser cannot start the background worker", "fail");
    return;
  }
  worker.onmessage = (e) => {
    const m = e.data;
    if (m.type === "status") {
      if (engineState === "loading") setEngine(m.text);
      if ($("#busy").classList.contains("on")) $("#busyText").textContent = m.text;
      return;
    }
    if (m.type === "ready") {
      engineState = "ready";
      setEngine("Engine ready", "ready");
      return;
    }
    if (m.type === "init-error") {
      engineState = "error";
      setEngine("The engine could not start", "fail");
      $("#busy").classList.remove("on");
      showError("<b>The engine could not start.</b> " + esc(m.error) +
        " The page needs internet access to download Python (Pyodide) and the PDF library the first time. Reload to try again.");
      return;
    }
    if (m.id && pending.has(m.id)) {
      const p = pending.get(m.id);
      pending.delete(m.id);
      m.ok ? p.resolve(m.result) : p.reject(new Error(m.error));
    }
  };
  worker.onerror = () => {
    engineState = "error";
    setEngine("The engine could not start", "fail");
  };
}

function request(msg, transfer = []) {
  return new Promise((resolve, reject) => {
    if (!worker) return reject(new Error("The background worker is not available in this browser."));
    const id = ++seq;
    pending.set(id, { resolve, reject });
    worker.postMessage({ ...msg, id }, transfer);
  });
}

startWorker();

// ---- file picking -----------------------------------------------------------------------------
const drop = $("#drop"), fileIn = $("#file");
drop.addEventListener("click", () => fileIn.click());
drop.addEventListener("keydown", (e) => { if (e.key === "Enter" || e.key === " ") { e.preventDefault(); fileIn.click(); } });
["dragenter", "dragover"].forEach((t) => drop.addEventListener(t, (e) => { e.preventDefault(); drop.classList.add("over"); }));
["dragleave", "drop"].forEach((t) => drop.addEventListener(t, (e) => { e.preventDefault(); drop.classList.remove("over"); }));
drop.addEventListener("drop", (e) => { if (e.dataTransfer.files[0]) run(e.dataTransfer.files[0]); });
fileIn.addEventListener("change", () => { if (fileIn.files[0]) { run(fileIn.files[0]); fileIn.value = ""; } });

$("#sample").addEventListener("click", async () => {
  try {
    const r = await fetch("sample_bar.pdf");
    if (!r.ok) throw new Error("The sample file is missing from this deployment.");
    const blob = await r.blob();
    run(new File([blob], "sample_bar.pdf", { type: "application/pdf" }));
  } catch (err) {
    showError("<b>The sample could not be loaded.</b> " + esc(err.message));
  }
});

function showError(html) { const e = $("#error"); e.innerHTML = html; e.classList.add("on"); }

async function run(file) {
  $("#error").classList.remove("on");
  $("#out").classList.remove("on");
  $("#busy").classList.add("on");
  $("#busyText").textContent = engineState === "ready" ? "Reading " + file.name : "Starting the engine, then reading " + file.name;
  S.pdfDoc = null; S.crops = new Map();
  try {
    const t0 = performance.now();
    const buf = await file.arrayBuffer();
    const res = await request({ type: "analyze", buffer: buf, name: file.name }, [buf]);
    S.seconds = (performance.now() - t0) / 1000;
    S.res = res; S.sel = 0; S.file = file; S.zoomUser = false; S.tab = "ext";
    res.file = file.name;
    drop.classList.add("compact");
    $("#dropInner").innerHTML = "<span><strong>" + esc(file.name) + "</strong> &nbsp; " + res.page.w_mm + " × " + res.page.h_mm + " mm</span><span>Choose another PDF</span>";
    $("#busy").classList.remove("on");
    if (!res.candidates.length) {
      showError("<b>No linear color bar found.</b> The file has no row or column of four or more equal-size filled rectangles with at least three different fills. Check that the bar is live vector artwork and not an image.");
      return;
    }
    render();
  } catch (err) {
    $("#busy").classList.remove("on");
    showError("<b>That did not work.</b> " + esc(err.message));
  }
}

// ---- results ----------------------------------------------------------------------------------
const cand = () => S.res.candidates[S.sel];

function download(name, text, type) {
  const a = document.createElement("a");
  a.href = URL.createObjectURL(new Blob([text], { type }));
  a.download = name;
  a.click();
  setTimeout(() => URL.revokeObjectURL(a.href), 2000);
}

function render() {
  $("#out").classList.add("on");
  const c = cand(), v = c.orientation === "vertical";
  let h = '<h2>Patch size</h2><div class="body">';
  h += '<p class="size">' + fmt(c.patch_w_mm) + " × " + fmt(c.patch_h_mm) + " <small>mm</small></p>";
  h += '<p class="sizeNote">' + (v ? "Across × along the strip (width × height)." : "Width × height.") + "</p>";
  if (c.corner_mark) {
    const cm = c.corner_mark;
    h += '<p class="markNote">A corner mark (' + fmt(cm.size_mm, 1) + " mm) sits " + fmt(cm.gap_mm, 1) + " mm past the " +
      (cm.applied ? "last patch, so the bar is read from the patch next to it: slot 1." : "first patch, which is already slot 1.") + "</p>";
  }
  h += "<dl>";
  const dirText = c.direction.charAt(0).toUpperCase() + c.direction.slice(1);
  h += "<dt>Orientation</dt><dd>" + (v ? "Vertical" : "Horizontal") + "</dd>";
  h += "<dt>Reading order</dt><dd>" + dirText + "</dd>";
  h += "<dt>Corner mark</dt><dd>" + (c.corner_mark ? (c.corner_mark.applied ? "Found, sets slot 1" : "Found at the start") : "None") + "</dd>";
  h += "<dt>Pitch</dt><dd>" + fmt(c.pitch_mm) + " mm</dd>";
  h += "<dt>Slots</dt><dd>" + c.slot_count + "</dd>";
  h += "<dt>Ink patches</dt><dd>" + c.counts.ink + "</dd>";
  h += "<dt>Paper white</dt><dd>" + c.counts.paper + "</dd>";
  h += "<dt>NU (not used)</dt><dd>" + c.counts.nu + "</dd>";
  h += "<dt>Bar length</dt><dd>" + fmt(c.length_mm, 2) + " mm</dd>";
  h += "<dt>Bar starts, from left</dt><dd>" + fmt(c.start_mm.x, 2) + " mm</dd>";
  h += "<dt>Bar starts, from top</dt><dd>" + fmt(c.start_mm.y, 2) + " mm</dd>";
  h += "<dt>Page size</dt><dd>" + S.res.page.w_mm + " × " + S.res.page.h_mm + " mm</dd>";
  h += "<dt>Analysis time</dt><dd>" + fmt(S.seconds, 1) + " s</dd>";
  h += "</dl>";
  c.warnings.forEach((w) => (h += '<div class="warn">' + esc(w) + "</div>"));
  if (S.res.candidates.length > 1) {
    h += '<div class="cands"><label for="cs">More than one bar was found</label><select id="cs">';
    S.res.candidates.forEach((k, i) => (h += '<option value="' + i + '"' + (i === S.sel ? " selected" : "") + ">Bar " + (i + 1) + ": " + k.slot_count + " slots, " + fmt(k.patch_w_mm) + " × " + fmt(k.patch_h_mm) + " mm</option>"));
    h += "</select></div>";
  }
  (c.xml_notes || []).forEach((n) => (h += '<div class="warn">XML: ' + esc(n) + "</div>"));
  h += '<div class="btnrow"><button class="btn" id="dlXml">Download XML</button></div>';
  const ig = S.res.ignored;
  h += "<details><summary>What was ignored</summary><ul>";
  h += "<li>" + ig.images + " images</li><li>" + ig.text_runs + " text runs</li>";
  h += "<li>" + ig.curved_or_irregular_shapes + " curved or irregular shapes</li>";
  h += "<li>" + ig.production_ink_rectangles + " rectangles in production inks (dieline, registration, cut)</li>";
  if (ig.corner_marks) h += "<li>" + ig.corner_marks + " corner mark" + (ig.corner_marks > 1 ? "s" : "") + "</li>";
  h += "<li>" + ig.other_rectangles + " other rectangles that do not form a bar</li></ul></details>";
  h += "</div>";
  $("#summary").innerHTML = h;

  const cs = $("#cs");
  if (cs) cs.onchange = () => { S.sel = +cs.value; S.zoomUser = false; render(); };
  $("#dlXml").onclick = () => download("colorbar_" + S.res.file.replace(/\.pdf$/i, "") + ".xml", cand().xml, "application/xml");
  fillTable();
  setTab(S.tab);
}

function fillTable() {
  const c = cand(), tb = $("#tbl tbody");
  tb.innerHTML = "";
  c.patches.forEach((p, i) => {
    const tr = document.createElement("tr");
    tr.dataset.i = i;
    if (p.kind === "nu") tr.className = "nu";
    const sw = p.kind === "nu" ? '<span class="sw nu"></span>' : '<span class="sw" style="background:' + p.rgb + '"></span>';
    tr.innerHTML = '<td class="n">' + p.slot + "</td>" +
      '<td class="n">' + fmt(p.along_mm) + "</td>" +
      '<td class="n">' + fmt(p.w_mm) + " × " + fmt(p.h_mm) + "</td>" +
      "<td>" + sw + esc(p.label) + (p.detail ? ' <span class="detail">' + esc(p.detail) + "</span>" : "") + "</td>";
    tr.onmouseenter = () => hl(i, true);
    tr.onmouseleave = () => hl(i, false);
    tb.appendChild(tr);
  });
}

function hl(i, on) {
  document.querySelectorAll("#tbl tbody tr").forEach((r) => r.classList.toggle("hl", on && +r.dataset.i === i));
  const el = document.getElementById("p" + i);
  if (el) { el.setAttribute("stroke", on ? "#0071b0" : "rgba(0,0,0,.35)"); el.setAttribute("stroke-width", on ? 2.5 : 0.8); }
}

// ---- viewer tabs ------------------------------------------------------------------------------
$("#tab-ext").onclick = () => setTab("ext");
$("#tab-ren").onclick = () => setTab("ren");
$("#zoom").oninput = () => { S.zoomUser = true; S.zoomVal = +$("#zoom").value; drawBar(); };
window.addEventListener("resize", () => { if (S.res && S.res.candidates.length && S.tab === "ext" && !S.zoomUser) drawBar(); });

function setTab(t) {
  S.tab = t;
  $("#tab-ext").setAttribute("aria-selected", t === "ext");
  $("#tab-ren").setAttribute("aria-selected", t === "ren");
  $("#zoomWrap").style.display = t === "ext" ? "" : "none";
  t === "ext" ? drawBar() : drawRendered();
}

function lum(hex) { const n = parseInt(hex.slice(1), 16); return (0.299 * (n >> 16) + 0.587 * ((n >> 8) & 255) + 0.114 * (n & 255)) / 255; }

function drawBar() {
  const c = cand(), stage = $("#stage");
  const horizontal = c.orientation === "horizontal";
  const avail = Math.max(320, stage.clientWidth - 24);
  const cm = c.corner_mark;
  const pad = { l: 50 + (cm ? (cm.gap_mm + cm.size_mm) * Math.max(1, Math.min(12, (avail - 120) / c.along_len_mm)) : 8), t: 46, r: 40, b: 38 };
  const sc = S.zoomUser ? S.zoomVal : Math.max(3, Math.min(12, (avail - pad.l - pad.r) / c.along_len_mm));
  if (!S.zoomUser) $("#zoom").value = Math.round(sc * 2) / 2;
  const W = pad.l + c.along_len_mm * sc + pad.r, H = pad.t + Math.max(c.cross_len_mm * sc, 30) + pad.b;
  const first = c.patches.find((p) => !p.empty) || c.patches[0];
  const ox = pad.l, oy = pad.t, pitch = c.pitch_mm, every = Math.max(1, Math.ceil(46 / (pitch * sc)));
  let s = '<svg xmlns="http://www.w3.org/2000/svg" width="' + W + '" height="' + H + '" viewBox="0 0 ' + W + " " + H + '" font-family="Avenir Next,Segoe UI,Helvetica Neue,Arial,sans-serif">';

  // ruler
  s += '<line x1="' + ox + '" y1="' + (oy - 14) + '" x2="' + (ox + c.slot_count * pitch * sc) + '" y2="' + (oy - 14) + '" stroke="#566170"/>';
  for (let i = 0; i <= c.slot_count; i++) {
    const x = ox + i * pitch * sc, major = i % every === 0;
    s += '<line x1="' + x + '" y1="' + (oy - 14) + '" x2="' + x + '" y2="' + (oy - (major ? 22 : 18)) + '" stroke="#566170"/>';
    if (major) s += '<text x="' + x + '" y="' + (oy - 26) + '" font-size="10" fill="#566170" text-anchor="middle">' + fmt(i * pitch, (i * pitch) % 1 === 0 ? 0 : 1) + "</text>";
  }
  s += '<text x="' + (ox + c.slot_count * pitch * sc + 14) + '" y="' + (oy - 11) + '" font-size="10" fill="#566170">mm</text>';

  // one cell per slot: ink, paper white (dashed when nothing is drawn there), NU (hatched), or a split cell
  s += '<defs><pattern id="nuhatch" width="6" height="6" patternUnits="userSpaceOnUse" patternTransform="rotate(45)">' +
       '<rect width="6" height="6" fill="#eceff2"/><line x1="0" y1="0" x2="0" y2="6" stroke="#aab2bc" stroke-width="2"/></pattern></defs>';
  c.patches.forEach((p, i) => {
    const x = ox + p.along_mm * sc, y = oy + p.cross_mm * sc, w = p.len_mm * sc, h = p.thick_mm * sc;
    const fill = p.kind === "nu" ? "url(#nuhatch)" : p.rgb;
    const stroke = p.kind === "paper" && p.empty ? "#9aa4af" : "rgba(0,0,0,.35)";
    const dash = p.kind === "paper" && p.empty ? ' stroke-dasharray="4 3"' : "";
    s += '<rect id="p' + i + '" x="' + x + '" y="' + y + '" width="' + w + '" height="' + h + '" fill="' + fill + '" stroke="' + stroke + '" stroke-width="0.8"' + dash + '><title>' + esc("slot " + p.slot + ": " + p.label + (p.detail ? " (" + p.detail + ")" : "")) + "</title></rect>";
    p.parts.forEach((q) => {
      s += '<rect x="' + (ox + q.along_mm * sc) + '" y="' + (oy + q.cross_mm * sc) + '" width="' + q.len_mm * sc + '" height="' + q.thick_mm * sc + '" fill="' + q.rgb + '" stroke="rgba(0,0,0,.25)" stroke-width="0.6" pointer-events="none"><title>' + esc(q.label) + "</title></rect>";
    });
    const t = p.kind === "nu" ? "NU" : (p.kind === "ink" ? p.label : "");
    if (t && h >= 12 && w >= t.length * 5.6 + 4 && t.length <= 9) {
      s += '<text x="' + (x + w / 2) + '" y="' + (y + h / 2 + 3.5) + '" font-size="10" font-weight="600" text-anchor="middle" fill="' + (p.kind === "nu" ? "#566170" : (lum(p.rgb) > 0.55 ? "#1f252b" : "#ffffff")) + '" pointer-events="none">' + esc(t) + "</text>";
    }
  });

  // corner mark: three concentric squares, drawn before slot 1 (the end the bar is read from)
  if (cm) {
    const u = cm.size_mm / 7 * sc, mx = ox + cm.along_mm * sc, my = oy + cm.cross_mm * sc;
    s += '<rect x="' + mx + '" y="' + my + '" width="' + 7 * u + '" height="' + 7 * u + '" fill="#231f20"><title>Corner mark</title></rect>';
    s += '<rect x="' + (mx + u) + '" y="' + (my + u) + '" width="' + 5 * u + '" height="' + 5 * u + '" fill="#ffffff"/>';
    s += '<rect x="' + (mx + 2 * u) + '" y="' + (my + 2 * u) + '" width="' + 3 * u + '" height="' + 3 * u + '" fill="#231f20"/>';
    s += '<text x="' + (mx + 3.5 * u) + '" y="' + (oy - 4) + '" font-size="9" fill="#566170" text-anchor="middle">mark</text>';
  }

  // slot numbers
  const yb = oy + c.cross_len_mm * sc + 14;
  for (let i = 0; i < c.slot_count; i++) {
    if (pitch * sc >= 16 || i % every === 0) s += '<text x="' + (ox + (i + 0.5) * pitch * sc) + '" y="' + yb + '" font-size="9" fill="#566170" text-anchor="middle">' + (i + 1) + "</text>";
  }
  s += '<text x="' + (ox - 6) + '" y="' + yb + '" font-size="9" fill="#566170" text-anchor="end">slot</text>';

  // dimension callouts on the first patch
  if (first) {
    const x = ox + first.along_mm * sc, y = oy + first.cross_mm * sc, w = first.len_mm * sc, h = first.thick_mm * sc;
    const xe = ox + c.along_len_mm * sc;
    s += '<path d="M' + (xe + 10) + " " + y + "V" + (y + h) + "M" + (xe + 6) + " " + y + "H" + (xe + 14) + "M" + (xe + 6) + " " + (y + h) + "H" + (xe + 14) + '" stroke="#0071b0" stroke-width="1.2" fill="none"/>';
    s += '<text transform="translate(' + (xe + 24) + " " + (y + h / 2) + ') rotate(-90)" font-size="10" font-weight="600" fill="#0071b0" text-anchor="middle">' + fmt(first.thick_mm) + "</text>";
    s += '<text x="' + (x + w / 2) + '" y="' + (yb + 14) + '" font-size="10" font-weight="600" fill="#0071b0" text-anchor="middle">' + fmt(first.len_mm) + "</text>";
  }
  s += "</svg>";
  stage.innerHTML = s;
  let cap = horizontal
    ? "Patches are drawn to scale from the vector data in the PDF. Dimensions are in mm. Hatched cells are NU (not used). Dashed boxes are slots with nothing drawn. Scroll sideways or use Zoom on a long bar."
    : "This bar is vertical on the sheet. It is shown turned on its side so it fits the screen. Dimensions are in mm. Hatched cells are NU (not used). Dashed boxes are slots with nothing drawn.";
  if (c.direction === "right to left") cap += " The bar is read from right to left, so the preview is mirrored to start at slot 1.";
  if (c.direction === "bottom to top") cap += " The bar is read from the bottom up, so slot 1 is at the left.";
  if (c.direction === "top to bottom" && !horizontal) cap += " Slot 0 is the top patch.";
  $("#cap").textContent = cap;
}

// ---- "Rendered from the PDF" (pdf.js, loaded only when this tab is opened) ---------------------
let pdfjsPromise = null;
function loadPdfjs() {
  if (!pdfjsPromise) {
    pdfjsPromise = import(PDFJS_BASE + "pdf.min.mjs").then((m) => {
      m.GlobalWorkerOptions.workerSrc = PDFJS_BASE + "pdf.worker.min.mjs";
      return m;
    });
  }
  return pdfjsPromise;
}

async function drawRendered() {
  const stage = $("#stage");
  $("#cap").textContent = "";
  const key = S.sel;
  if (S.crops.has(key)) { mountCanvas(S.crops.get(key)); return; }
  stage.innerHTML = '<p class="note">Rendering the area around the bar from the PDF</p>';
  try {
    const pdfjs = await loadPdfjs();
    if (!S.pdfDoc) S.pdfDoc = await pdfjs.getDocument({ data: new Uint8Array(await S.file.arrayBuffer()) }).promise;
    const page = await S.pdfDoc.getPage(1);
    const c = cand(), b = c.bbox_pt, MM2PT = 72 / 25.4;
    const pw = S.res.page.w_mm * MM2PT, ph = S.res.page.h_mm * MM2PT, m = 6 * MM2PT;
    const left = Math.max(0, b.x0 - m), right = Math.min(pw, b.x1 + m);
    const top = Math.max(0, ph - b.y1 - m), bottom = Math.min(ph, ph - b.y0 + m);
    const longest = Math.max(right - left, bottom - top);
    const scale = Math.max(96, Math.min(300, 2600 / (longest / 72))) / 72;
    const canvas = document.createElement("canvas");
    canvas.width = Math.ceil((right - left) * scale);
    canvas.height = Math.ceil((bottom - top) * scale);
    const viewport = page.getViewport({ scale, offsetX: -left * scale, offsetY: -top * scale });
    await page.render({ canvasContext: canvas.getContext("2d"), viewport, background: "#ffffff" }).promise;
    canvas.onclick = () => canvas.classList.toggle("full");
    S.crops.set(key, canvas);
    if (S.tab === "ren" && S.sel === key) mountCanvas(canvas);
  } catch (err) {
    stage.innerHTML = '<p class="note">The preview could not be rendered (' + esc(err.message || err) + "). The extracted bar and the patch map are not affected.</p>";
  }
}

function mountCanvas(canvas) {
  const stage = $("#stage");
  stage.innerHTML = "";
  stage.appendChild(canvas);
  $("#cap").textContent = "Straight from the PDF with 6 mm of surrounding sheet, in its true orientation. Click the image to zoom. Labels and marks beside the bar are not part of the patch map.";
}
