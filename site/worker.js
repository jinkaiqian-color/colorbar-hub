// Runs the Python engine (Pyodide + pypdf + colorbar_core.py) off the main thread.
// The PDF never leaves the browser: it is written into Pyodide's in-memory file system.
//
// Pyodide 314 and later no longer support classic workers, so this file is loaded as
// an ES module worker (see app.js) and imports Pyodide as a module.
import { loadPyodide } from "https://cdn.jsdelivr.net/pyodide/v314.0.7/full/pyodide.mjs";

const PYODIDE_INDEX = "https://cdn.jsdelivr.net/pyodide/v314.0.7/full/";
const PYPDF_REQUIREMENT = "pypdf==5.9.0"; // the version the engine is tested with

const post = (message) => self.postMessage(message);

const ready = (async () => {
  post({ type: "status", text: "Loading the Python engine (about 10 MB, cached after the first visit)" });
  const py = await loadPyodide({ indexURL: PYODIDE_INDEX });

  post({ type: "status", text: "Installing the PDF reader" });
  await py.loadPackage("micropip");
  await py.runPythonAsync(`import micropip\nawait micropip.install("${PYPDF_REQUIREMENT}")`);

  post({ type: "status", text: "Loading the color bar engine" });
  const response = await fetch("colorbar_core.py", { cache: "no-cache" });
  if (!response.ok) throw new Error("colorbar_core.py could not be downloaded (HTTP " + response.status + ").");
  py.FS.writeFile("/tmp/colorbar_core.py", await response.text());
  py.runPython("import sys\nif '/tmp' not in sys.path:\n    sys.path.insert(0, '/tmp')\nimport colorbar_core");

  post({ type: "ready" });
  return py;
})();

ready.catch((err) => post({ type: "init-error", error: String((err && err.message) || err) }));

function friendly(err) {
  const text = String((err && err.message) || err);
  if (/encrypt|password|decrypt/i.test(text)) {
    return "This PDF is password-protected. Remove the password and try again.";
  }
  if (/EOF marker|startxref|not a PDF|PdfReadError|Stream has ended/i.test(text)) {
    return "This file could not be read as a PDF. It may be damaged or incomplete.";
  }
  const lines = text.split("\n").map((l) => l.trim()).filter(Boolean);
  return lines.length ? lines[lines.length - 1] : "The PDF could not be analyzed.";
}

self.onmessage = async (event) => {
  const { id, type } = event.data;
  try {
    const py = await ready;
    if (type !== "analyze") throw new Error("Unknown request: " + type);

    post({ type: "status", text: "Reading the PDF" });
    py.FS.writeFile("/tmp/in.pdf", new Uint8Array(event.data.buffer));
    py.globals.set("pdf_name", String(event.data.name || "document.pdf"));
    let json;
    try {
      json = py.runPython(
        "import json, colorbar_core\n" +
        "json.dumps(colorbar_core.analyze_for_web('/tmp/in.pdf', pdf_name))"
      );
    } finally {
      try { py.FS.unlink("/tmp/in.pdf"); } catch (_) { /* already gone */ }
    }
    post({ id, ok: true, result: JSON.parse(json) });
  } catch (err) {
    post({ id, ok: false, error: friendly(err) });
  }
};
