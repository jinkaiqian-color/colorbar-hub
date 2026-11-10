"""
colorbar_core - find the linear color bar in a press-sheet PDF.

Pure Python plus pypdf, so the same file runs under CPython (command line,
tests) and inside the browser through Pyodide (the public web page).

Main entry points
-----------------
analyze(source, name=None)         -> dict   source: file path, bytes, or binary file object
analyze_for_web(source, name=None) -> dict   same, plus an XML string for each candidate bar
                                             (name overrides the file name shown in the result)
candidate_xml(result, candidate) -> str   the bar in the colorbar template XML format

How the bar is found
--------------------
1. Every filled rectangle is collected with its position, size and ink.
   Shapes filled with production inks (dieline, registration "All", cut,
   crease ...) are set aside. Images, text and curved shapes are never bar
   patches.
2. Rectangles of the same size are grouped. A color bar shows up as a group
   whose members sit on one straight line, advance by a constant pitch, and
   use at least three different fills.
3. The best-scoring line wins. Every slot along it gets one record, so the
   slot index never skips. A slot is "ink", "paper" (a white patch, or nothing
   drawn) or "nu" (not used: it holds a special mark such as a circle, box,
   tick marks, an image or a label). A cell split into parts across its width
   (a tint ladder) stays one slot of kind "split".
4. Step and repeat: when the same core design appears several times along the bar (or on several
   lines), only one core is reported and the repeats are flagged.
5. If a QR-style corner mark (three concentric squares in the ratio 7 : 5 : 3)
   sits in line with the bar, just past one end, the patch next to the mark
   becomes the first patch and the bar is read away from it.
"""
import io
import math
import os
import re
import statistics
import xml.etree.ElementTree as ET
from collections import Counter

from pypdf import PdfReader
from pypdf.generic import ContentStream

__version__ = "1.8.0"

PT2MM = 25.4 / 72.0
MM2PT = 72.0 / 25.4

# --------------------------------------------------------------------------
# Matrices
# --------------------------------------------------------------------------


def mmul(m, n):
    """Matrix that applies m first, then n."""
    a1, b1, c1, d1, e1, f1 = m
    a2, b2, c2, d2, e2, f2 = n
    return (
        a1 * a2 + b1 * c2,
        a1 * b2 + b1 * d2,
        c1 * a2 + d1 * c2,
        c1 * b2 + d1 * d2,
        e1 * a2 + f1 * c2 + e2,
        e1 * b2 + f1 * d2 + f2,
    )


def mapply(m, x, y):
    return (m[0] * x + m[2] * y + m[4], m[1] * x + m[3] * y + m[5])


IDENTITY = (1.0, 0.0, 0.0, 1.0, 0.0, 0.0)

# --------------------------------------------------------------------------
# Inks and color spaces
# --------------------------------------------------------------------------

PROCESS_NAMES = {"cyan": "C", "magenta": "M", "yellow": "Y", "black": "K"}
PROCESS_RGB = {
    "C": (0, 174, 239),
    "M": (236, 0, 140),
    "Y": (255, 242, 0),
    "K": (35, 31, 32),
}
PRODUCTION_RE = re.compile(
    r"die|cutter|^cut|crease|perf|fold|bleed|safe|trim|guide|regist|^all$|"
    r"dimension|technical|keyline",
    re.I,
)
COATING_RE = re.compile(r"varnish|opv|coat|gloss|matte|lacquer|aqueous|seal", re.I)
FALLBACK_SPOT_RGB = [(230, 134, 46), (63, 163, 77), (139, 92, 246), (200, 60, 90), (60, 130, 200)]


def ink_role(name):
    low = name.lower().strip()
    if low in PROCESS_NAMES:
        return "process"
    if low == "none":
        return "none"
    if PRODUCTION_RE.search(low):
        return "production"
    if COATING_RE.search(low):
        return "coating"
    if "white" in low:
        return "white"
    return "spot"


def _floats(arr, default):
    try:
        return [float(x) for x in arr]
    except Exception:
        return default


def _fn_info(fn_obj):
    """Only exponential interpolation (type 2) is evaluated; others fall back."""
    try:
        fn = fn_obj.get_object()
        if int(fn.get("/FunctionType", -1)) == 2:
            return {
                "type": 2,
                "c0": _floats(fn.get("/C0", [0.0]), [0.0]),
                "c1": _floats(fn.get("/C1", [1.0]), [1.0]),
                "n": float(fn.get("/N", 1)),
            }
    except Exception:
        pass
    return None


def parse_colorspace(obj):
    """Return a small dict describing a PDF color space."""
    try:
        obj = obj.get_object()
    except Exception:
        pass
    if isinstance(obj, str):
        name = str(obj).lstrip("/")
        table = {
            "DeviceGray": "DeviceGray", "G": "DeviceGray", "CalGray": "DeviceGray",
            "DeviceRGB": "DeviceRGB", "RGB": "DeviceRGB", "CalRGB": "DeviceRGB",
            "DeviceCMYK": "DeviceCMYK", "CMYK": "DeviceCMYK",
        }
        return {"type": table.get(name, "Other"), "names": [], "alt": None, "fn": None}
    if isinstance(obj, list) and obj:
        kind = str(obj[0]).lstrip("/")
        if kind == "Separation" and len(obj) >= 4:
            return {
                "type": "Separation",
                "names": [str(obj[1]).lstrip("/")],
                "alt": parse_colorspace(obj[2])["type"],
                "fn": _fn_info(obj[3]),
            }
        if kind == "DeviceN" and len(obj) >= 4:
            names = [str(n).lstrip("/") for n in obj[1].get_object()]
            return {
                "type": "DeviceN",
                "names": names,
                "alt": parse_colorspace(obj[2])["type"],
                "fn": _fn_info(obj[3]),
            }
        if kind == "ICCBased" and len(obj) >= 2:
            try:
                n = int(obj[1].get_object().get("/N", 3))
            except Exception:
                n = 3
            return {"type": {1: "DeviceGray", 3: "DeviceRGB", 4: "DeviceCMYK"}.get(n, "Other"),
                    "names": [], "alt": None, "fn": None}
        if kind in ("CalGray",):
            return {"type": "DeviceGray", "names": [], "alt": None, "fn": None}
        if kind in ("CalRGB",):
            return {"type": "DeviceRGB", "names": [], "alt": None, "fn": None}
    return {"type": "Other", "names": [], "alt": None, "fn": None}


DEVICE_CS = {
    "DeviceGray": {"type": "DeviceGray", "names": [], "alt": None, "fn": None},
    "DeviceRGB": {"type": "DeviceRGB", "names": [], "alt": None, "fn": None},
    "DeviceCMYK": {"type": "DeviceCMYK", "names": [], "alt": None, "fn": None},
}


def initial_values(cs):
    t = cs["type"]
    if t == "DeviceCMYK":
        return [0.0, 0.0, 0.0, 1.0]
    if t == "DeviceRGB":
        return [0.0, 0.0, 0.0]
    if t == "DeviceGray":
        return [0.0]
    if t in ("Separation", "DeviceN"):
        return [1.0] * max(1, len(cs["names"]))
    return [0.0]


# --------------------------------------------------------------------------
# Fill decoding and preview colors
# --------------------------------------------------------------------------


def mix_process(c, m, y, k):
    """Subtractive mix of the four process inks on white (0..1 in, 0..255 out)."""
    rgb = [1.0, 1.0, 1.0]
    for key, t in (("C", c), ("M", m), ("Y", y), ("K", k)):
        t = max(0.0, min(1.0, t))
        for i in range(3):
            rgb[i] *= 1.0 - t * (1.0 - PROCESS_RGB[key][i] / 255.0)
    return tuple(v * 255.0 for v in rgb)


def sep_rgb(cs, tint, spot_index):
    """Approximate preview color of a spot ink at a given tint (0..1)."""
    fn = cs.get("fn")
    if fn and fn["type"] == 2 and cs.get("alt") in ("DeviceCMYK", "DeviceRGB", "DeviceGray"):
        t = max(0.0, min(1.0, tint)) ** fn["n"]
        vals = [a + t * (b - a) for a, b in zip(fn["c0"], fn["c1"])]
        if cs["alt"] == "DeviceCMYK" and len(vals) >= 4:
            return mix_process(*vals[:4])
        if cs["alt"] == "DeviceRGB" and len(vals) >= 3:
            return tuple(v * 255.0 for v in vals[:3])
        if cs["alt"] == "DeviceGray" and vals:
            return (vals[0] * 255.0,) * 3
    base = FALLBACK_SPOT_RGB[spot_index % len(FALLBACK_SPOT_RGB)]
    return tuple(255.0 - tint * (255.0 - v) for v in base)


class Fill:
    __slots__ = ("cs", "vals")

    def __init__(self, cs, vals):
        self.cs = cs
        self.vals = vals


def decode_fill(fill):
    """-> (inks, flags). inks: list of (key, tint 0..1); key is C/M/Y/K or 'spot:<name>'."""
    cs, vals = fill.cs, fill.vals
    t = cs["type"]
    inks, flags = [], set()
    eps = 0.0005
    if t == "DeviceCMYK" and len(vals) >= 4:
        for key, v in zip("CMYK", vals[:4]):
            if v > eps:
                inks.append((key, v))
    elif t == "DeviceGray" and vals:
        if 1.0 - vals[0] > eps:
            inks.append(("K", 1.0 - vals[0]))
    elif t == "DeviceRGB":
        flags.add("rgb")
    elif t in ("Separation", "DeviceN"):
        for name, v in zip(cs["names"], vals):
            if v <= eps:
                continue
            role = ink_role(name)
            if role == "process":
                inks.append((PROCESS_NAMES[name.lower().strip()], v))
            elif role == "none":
                continue
            elif role == "production":
                flags.add("production")
                inks.append(("spot:" + name, v))
            else:
                if role == "coating":
                    flags.add("coating")
                inks.append(("spot:" + name, v))
    else:
        flags.add("other")
    return inks, flags


def fill_signature(fill):
    inks, flags = decode_fill(fill)
    sig = tuple(sorted((k, round(v, 3)) for k, v in inks))
    if "rgb" in flags:
        sig = ("rgb",) + tuple(round(v, 3) for v in fill.vals)
    return sig, flags


def fill_preview_rgb(fill, spot_order):
    inks, flags = decode_fill(fill)
    cs, vals = fill.cs, fill.vals
    if cs["type"] == "DeviceRGB" and len(vals) >= 3:
        return tuple(v * 255.0 for v in vals[:3])
    rgb = [1.0, 1.0, 1.0]
    for key, tint in inks:
        if key.startswith("spot:"):
            name = key[5:]
            idx = spot_order.index(name) if name in spot_order else 0
            single = cs["type"] == "Separation"
            comp = sep_rgb(cs if single else {"fn": None, "alt": None}, tint, idx)
        else:
            comp = mix_process(*(tint if key == k else 0.0 for k in "CMYK"))
        for i in range(3):
            rgb[i] *= comp[i] / 255.0
    return tuple(v * 255.0 for v in rgb)


def hexrgb(rgb):
    return "#%02x%02x%02x" % tuple(max(0, min(255, int(round(v)))) for v in rgb)


# --------------------------------------------------------------------------
# Content stream interpreter (collects filled rectangles)
# --------------------------------------------------------------------------


class GState:
    __slots__ = ("ctm", "fill", "stroke", "lw")

    def __init__(self, ctm=IDENTITY, fill=None, stroke=None, lw=1.0):
        self.ctm = ctm
        self.fill = fill or Fill(DEVICE_CS["DeviceGray"], [0.0])
        self.stroke = stroke or Fill(DEVICE_CS["DeviceGray"], [0.0])
        self.lw = lw

    def copy(self):
        return GState(self.ctm, Fill(self.fill.cs, list(self.fill.vals)),
                      Fill(self.stroke.cs, list(self.stroke.vals)), self.lw)


PAINT_FILL = {"f", "F", "f*", "B", "B*", "b", "b*"}
CLOSE_PAINT = {"b", "b*", "s"}


def _simplify_polygon(pts, tol=0.02):
    """Drop a closing duplicate and collinear vertices, so a rectangle that was
    written with extra points (for example starting mid-edge) is still a rectangle."""
    pts = list(pts)
    if len(pts) > 1 and abs(pts[0][0] - pts[-1][0]) < tol and abs(pts[0][1] - pts[-1][1]) < tol:
        pts.pop()
    changed = True
    while changed and len(pts) > 3:
        changed = False
        n = len(pts)
        for i in range(n):
            a, b, c = pts[i - 1], pts[i], pts[(i + 1) % n]
            dx, dy = c[0] - a[0], c[1] - a[1]
            length = math.hypot(dx, dy)
            if length < 1e-9:
                dist = math.hypot(b[0] - a[0], b[1] - a[1])
            else:
                dist = abs((b[0] - a[0]) * dy - (b[1] - a[1]) * dx) / length
            if dist < tol:
                del pts[i]
                changed = True
                break
    return pts


# rough character widths (fractions of the font size), enough to find the middle of a short label
_CHAR_WIDTH = {" ": 0.278, "%": 0.889, "+": 0.584, "[": 0.278, "]": 0.278, ".": 0.278, "-": 0.333,
               **{str(d): 0.556 for d in range(10)}}


class Collector:
    def __init__(self, reader):
        self.reader = reader
        self.rects = []  # dicts with x0,y0,x1,y1 (pt, user space) and fill
        self.shapes = []  # everything else that marks the page: curves, outlines, text, images
        self.texts = []   # the strings written on the page, each with the centre of where it is drawn
        self.lines = []   # straight, axis-aligned stroked lines: end points, width and ink
        self.stats = Counter()
        self._cs_cache = {}

    # -- helpers ----------------------------------------------------------
    @staticmethod
    def _get(d, key):
        try:
            v = d.get(key)
            return v.get_object() if v is not None else None
        except Exception:
            return None

    def _lookup_cs(self, name, resources):
        key = (id(resources), str(name))
        if key in self._cs_cache:
            return self._cs_cache[key]
        n = str(name).lstrip("/")
        if n in DEVICE_CS:
            cs = DEVICE_CS[n]
        else:
            table = self._get(resources, "/ColorSpace") or {}
            entry = table.get("/" + n) if hasattr(table, "get") else None
            cs = parse_colorspace(entry) if entry is not None else {"type": "Other", "names": [], "alt": None, "fn": None}
        self._cs_cache[key] = cs
        return cs

    # -- main loop --------------------------------------------------------
    def run(self, ops, resources, gs, depth=0):
        stack = []
        subpaths = []
        cur = None
        tm = tlm = IDENTITY          # text matrix and text line matrix
        font_size, leading = 1.0, 0.0
        for operands, op in ops:
            op = op.decode("latin1") if isinstance(op, bytes) else str(op)

            if op == "q":
                stack.append(gs.copy())
            elif op == "Q":
                if stack:
                    gs = stack.pop()
            elif op == "cm":
                try:
                    gs.ctm = mmul(tuple(float(x) for x in operands), gs.ctm)
                except Exception:
                    pass

            # colors
            elif op == "g":
                gs.fill = Fill(DEVICE_CS["DeviceGray"], [float(operands[0])])
            elif op == "rg":
                gs.fill = Fill(DEVICE_CS["DeviceRGB"], [float(x) for x in operands[:3]])
            elif op == "k":
                gs.fill = Fill(DEVICE_CS["DeviceCMYK"], [float(x) for x in operands[:4]])
            elif op == "cs":
                cs = self._lookup_cs(operands[0], resources)
                gs.fill = Fill(cs, initial_values(cs))
            # stroke colour and line width (needed to read lines drawn as strokes)
            elif op == "w":
                try:
                    gs.lw = float(operands[0])
                except Exception:
                    pass
            elif op == "G":
                gs.stroke = Fill(DEVICE_CS["DeviceGray"], [float(operands[0])])
            elif op == "RG":
                gs.stroke = Fill(DEVICE_CS["DeviceRGB"], [float(x) for x in operands[:3]])
            elif op == "K":
                gs.stroke = Fill(DEVICE_CS["DeviceCMYK"], [float(x) for x in operands[:4]])
            elif op == "CS":
                cs = self._lookup_cs(operands[0], resources)
                gs.stroke = Fill(cs, initial_values(cs))
            elif op in ("SC", "SCN"):
                vals = []
                for x in operands:
                    try:
                        vals.append(float(x))
                    except Exception:
                        pass
                if vals:
                    gs.stroke = Fill(gs.stroke.cs, vals)
            elif op in ("sc", "scn"):
                vals = []
                for x in operands:
                    try:
                        vals.append(float(x))
                    except Exception:
                        pass
                if vals:
                    gs.fill = Fill(gs.fill.cs, vals)

            # path construction
            elif op == "re":
                x, y, w, h = (float(v) for v in operands[:4])
                subpaths.append({"pts": [(x, y), (x + w, y), (x + w, y + h), (x, y + h)], "curved": False})
                cur = None
            elif op == "m":
                cur = {"pts": [(float(operands[0]), float(operands[1]))], "curved": False}
                subpaths.append(cur)
            elif op == "l":
                if cur is not None:
                    cur["pts"].append((float(operands[0]), float(operands[1])))
            elif op in ("c", "v", "y"):
                if cur is not None:
                    cur["curved"] = True
                    v = [float(x) for x in operands]
                    # control points are enough for a bounding box
                    cur["pts"].extend((v[i], v[i + 1]) for i in range(0, len(v) - 1, 2))
            elif op == "h":
                pass

            # painting
            elif op in PAINT_FILL or op in CLOSE_PAINT:
                if op in PAINT_FILL or op == "b":
                    self._paint(subpaths, gs)
                if op in ("B", "B*", "b", "b*"):
                    self._outline(subpaths, gs)
                subpaths, cur = [], None
            elif op in ("S", "s"):
                self._outline(subpaths, gs)
                subpaths, cur = [], None
            elif op == "n":
                subpaths, cur = [], None

            # text: only its approximate position matters (is something written in this cell?)
            elif op == "BT":
                tm = tlm = IDENTITY
            elif op == "Tf":
                try:
                    font_size = abs(float(operands[1])) or 1.0
                except Exception:
                    pass
            elif op == "TL":
                try:
                    leading = float(operands[0])
                except Exception:
                    pass
            elif op == "Tm":
                try:
                    tm = tlm = tuple(float(x) for x in operands[:6])
                except Exception:
                    pass
            elif op in ("Td", "TD"):
                try:
                    tx, ty = float(operands[0]), float(operands[1])
                    if op == "TD":
                        leading = -ty
                    tm = tlm = mmul((1, 0, 0, 1, tx, ty), tlm)
                except Exception:
                    pass
            elif op == "T*":
                tm = tlm = mmul((1, 0, 0, 1, 0, -leading), tlm)
            elif op in ("Tj", "TJ", "'", '"'):
                self.stats["text"] += 1
                if op in ("'", '"'):
                    tm = tlm = mmul((1, 0, 0, 1, 0, -leading), tlm)
                self._text(operands, op, mmul(tm, gs.ctm), font_size)

            # other content
            elif op in ("BI", "INLINE IMAGE"):
                self.stats["images"] += 1
                self._image(gs)
            elif op == "sh":
                self.stats["shadings"] += 1
            elif op == "Do":
                self._do(operands, resources, gs, depth)

    def _do(self, operands, resources, gs, depth):
        xobjs = self._get(resources, "/XObject") or {}
        name = str(operands[0])
        ref = xobjs.get(name) if hasattr(xobjs, "get") else None
        if ref is None:
            return
        try:
            xo = ref.get_object()
            sub = str(xo.get("/Subtype", ""))
        except Exception:
            return
        if sub == "/Image":
            self.stats["images"] += 1
            self._image(gs)
            return
        if sub != "/Form" or depth > 12:
            return
        matrix = _floats(xo.get("/Matrix", list(IDENTITY)), list(IDENTITY))
        if len(matrix) != 6:
            matrix = list(IDENTITY)
        new_gs = gs.copy()
        new_gs.ctm = mmul(tuple(matrix), gs.ctm)
        res = self._get(xo, "/Resources") or resources
        try:
            ops = ContentStream(xo, self.reader).operations
        except Exception:
            return
        self.stats["forms"] += 1
        self.run(ops, res, new_gs, depth + 1)

    def _add_shape(self, kind, pts):
        xs = [p[0] for p in pts]
        ys = [p[1] for p in pts]
        if xs:
            self.shapes.append({"kind": kind, "x0": min(xs), "y0": min(ys), "x1": max(xs), "y1": max(ys)})

    def _outline(self, subpaths, gs):
        scale = math.hypot(gs.ctm[0], gs.ctm[1])
        for sp in subpaths:
            tp = [mapply(gs.ctm, x, y) for x, y in sp["pts"]]
            self._add_shape("curve" if sp["curved"] else "outline", tp)
            if not sp["curved"] and len(tp) == 2:           # one straight segment: maybe a line of a line patch
                (xa, ya), (xb, yb) = tp
                if abs(xa - xb) < 0.02 or abs(ya - yb) < 0.02:
                    self.lines.append({"xa": xa, "ya": ya, "xb": xb, "yb": yb, "w": gs.lw * scale,
                                       "stroke": Fill(gs.stroke.cs, list(gs.stroke.vals))})

    def _image(self, gs):
        self._add_shape("image", [mapply(gs.ctm, x, y) for x, y in ((0, 0), (1, 0), (1, 1), (0, 1))])

    def _text(self, operands, op, m, font_size):
        n = 0
        parts = []
        for o in operands:
            if isinstance(o, (str, bytes)):
                n += len(o)
                parts.append(o)
            elif isinstance(o, list):
                n += sum(len(x) for x in o if isinstance(x, (str, bytes)))
                parts.extend(x for x in o if isinstance(x, (str, bytes)))
        if not n:
            return
        text = "".join(x if isinstance(x, str) else x.decode("latin-1") for x in parts)
        width = sum(_CHAR_WIDTH.get(ch, 0.6) for ch in text) * font_size
        cx, cy = mapply(m, width / 2, 0.3 * font_size)
        self.texts.append({"s": text, "cx": cx, "cy": cy})
        # rough box: 0.5 em per character wide, ascender and descender in height
        w, h0, h1 = 0.5 * font_size * n, -0.2 * font_size, 0.8 * font_size
        self._add_shape("text", [mapply(m, 0, h0), mapply(m, w, h0), mapply(m, w, h1), mapply(m, 0, h1)])

    def _paint(self, subpaths, gs):
        for sp in subpaths:
            pts = sp["pts"]
            if sp["curved"]:
                self.stats["curved_shapes"] += 1
                self._add_shape("curve", [mapply(gs.ctm, x, y) for x, y in pts])
                continue
            tp = _simplify_polygon([mapply(gs.ctm, x, y) for x, y in pts])
            if len(tp) != 4:
                self.stats["other_shapes"] += 1
                self._add_shape("shape", tp)
                continue
            xs = [p[0] for p in tp]
            ys = [p[1] for p in tp]
            bx0, bx1, by0, by1 = min(xs), max(xs), min(ys), max(ys)
            tol = 0.02
            ok = all(
                (abs(p[0] - bx0) < tol or abs(p[0] - bx1) < tol)
                and (abs(p[1] - by0) < tol or abs(p[1] - by1) < tol)
                for p in tp
            ) and len({(round(p[0], 1), round(p[1], 1)) for p in tp}) == 4
            if not ok or bx1 - bx0 < 0.05 or by1 - by0 < 0.05:
                self.stats["other_shapes"] += 1
                self._add_shape("shape", tp)
                continue
            self.rects.append(
                {"x0": bx0, "y0": by0, "x1": bx1, "y1": by1, "fill": Fill(gs.fill.cs, list(gs.fill.vals))}
            )


# --------------------------------------------------------------------------
# Bar detection
# --------------------------------------------------------------------------


def _cluster(values, tol):
    """Greedy 1-D clustering of (value, item) pairs; returns list of item lists."""
    values = sorted(values, key=lambda p: p[0])
    groups, last = [], None
    for v, item in values:
        if last is not None and v - last <= tol:
            groups[-1].append(item)
        else:
            groups.append([item])
        last = v
    return groups


def evaluate_line(members, axis):
    """members: rect dicts of equal size on one line. axis 'x' (horizontal) or 'y' (vertical)."""
    key = "x0" if axis == "x" else "top"
    size_key = "w" if axis == "x" else "h"
    members = sorted(members, key=lambda r: r[key])
    # drop duplicates sitting on the same position
    uniq = []
    for r in members:
        if uniq and abs(r[key] - uniq[-1][key]) < 0.5:
            continue
        uniq.append(r)
    members = uniq
    if len(members) < 4:
        return None
    starts = [r[key] for r in members]
    steps = [b - a for a, b in zip(starts, starts[1:])]
    size_along = statistics.mean(r[size_key] for r in members)
    # the pitch is the most common step (ties go to the smaller one), refined by averaging
    common = Counter(round(s / 0.5) * 0.5 for s in steps)
    top = max(common.values())
    base0 = min(v for v, n in common.items() if n == top)
    singles = [s for s in steps if abs(s - base0) <= max(0.6, 0.02 * base0)]
    base = statistics.mean(singles)
    if base < size_along * 0.95:
        return None
    conform = 0
    for s in steps:
        k = max(1, round(s / base))
        if abs(s - k * base) <= max(0.8, 0.02 * base):
            conform += 1
    if conform < 0.8 * len(steps):
        return None
    sigs = {r["sig"] for r in members}
    if len(sigs) < 3:
        return None
    return {
        "members": members,
        "pitch": base,
        "conform": conform / len(steps),
        "distinct": len(sigs),
        "axis": axis,
    }


def find_bars(rects, page_w, page_h, include_rgb=False):
    elig = []
    for r in rects:
        w, h = r["x1"] - r["x0"], r["y1"] - r["y0"]
        r["w"], r["h"] = w, h
        r["top"] = page_h - r["y1"]
        if min(w, h) < 1.5 * MM2PT or max(w, h) > 60 * MM2PT:
            continue
        if "production" in r["flags"] or "other" in r["flags"]:
            continue
        if "rgb" in r["flags"] and not include_rgb:
            continue
        elig.append(r)

    # cluster by size (both dimensions within ~0.2 mm)
    clusters = []
    for r in elig:
        for c in clusters:
            if abs(c["w"] - r["w"]) < 0.6 and abs(c["h"] - r["h"]) < 0.6:
                c["items"].append(r)
                break
        else:
            clusters.append({"w": r["w"], "h": r["h"], "items": [r]})

    found = []
    for c in clusters:
        if len(c["items"]) < 4:
            continue
        for axis, cross in (("x", "top"), ("y", "x0")):
            for line in _cluster([(r[cross], r) for r in c["items"]], 1.0):
                ev = evaluate_line(line, axis)
                if ev:
                    ev["size"] = (c["w"], c["h"])
                    ev["score"] = len(ev["members"]) + 2 * ev["distinct"]
                    found.append(ev)
    found.sort(key=lambda e: -e["score"])
    return found


# A QR finder pattern is a 7x7 square with a 5x5 light square and a 3x3 square inside it,
# all sharing one centre. The ratios below allow for small drawing tolerances.
MARK_MIN_MM, MARK_MAX_MM = 1.5, 15.0
MARK_MIDDLE_RATIO = (1.28, 1.52)   # outer / middle, ideal 7/5 = 1.40
MARK_INNER_RATIO = (2.10, 2.60)    # outer / inner,  ideal 7/3 = 2.33


def find_corner_marks(rects):
    """Find QR-style corner marks: concentric squares in the ratio 7 : 5 : 3.

    A mark whose light middle square was not drawn as a shape (a frame with a hole)
    is accepted with just the outer and inner squares (kind 'partial'). A mark only
    matters when it also sits in line with a color bar, which is checked later."""
    squares = []
    for r in rects:
        w, h = r["x1"] - r["x0"], r["y1"] - r["y0"]
        # the minimum size applies to the whole mark (its outer square), not to each layer
        if abs(w - h) <= max(0.3, 0.02 * w) and 0.5 * MM2PT <= w <= MARK_MAX_MM * MM2PT:
            squares.append({"cx": (r["x0"] + r["x1"]) / 2, "cy": (r["y0"] + r["y1"]) / 2,
                            "s": (w + h) / 2, "r": r})
    squares.sort(key=lambda q: -q["s"])
    marks, used = [], set()
    for o in squares:
        if id(o["r"]) in used or o["s"] < MARK_MIN_MM * MM2PT:
            continue
        tol = max(0.35, 0.03 * o["s"])

        def nested(q, lo, hi):
            return (q is not o and id(q["r"]) not in used
                    and abs(q["cx"] - o["cx"]) <= tol and abs(q["cy"] - o["cy"]) <= tol
                    and lo <= o["s"] / q["s"] <= hi)

        mids = [q for q in squares if nested(q, *MARK_MIDDLE_RATIO)]
        inns = [q for q in squares if nested(q, *MARK_INNER_RATIO)]
        if not inns:
            continue
        parts = [o, inns[0]] + (mids[:1])
        for q in parts:
            used.add(id(q["r"]))
        marks.append({"cx": o["cx"], "cy": o["cy"], "size": o["s"],
                      "kind": "finder" if mids else "partial", "rects": [q["r"] for q in parts]})
    return marks


STD_TINTS = {25: ("Q", 1), 50: ("H", 2), 75: ("T", 3)}      # tint -> (ID letter, RasterClass)
NU_RGB = (223, 227, 232)
MIN_MARK_MM = 0.04          # anything smaller than this is treated as a drawing artifact
PART_SPAN = 0.9             # a filled rectangle this wide across the cell is a part of the cell, not a mark
GRID_TOL_MM = 0.05          # a patch this far off the pitch grid is reported


def _describe_fill(fill, spot_label, spot_order):
    inks, flags = decode_fill(fill)
    ink_out, parts = [], []
    for key, t in inks:
        lab = spot_label[key[5:]] if key.startswith("spot:") else key
        pct = int(round(t * 100))
        ink_out.append({"ink": lab, "tint": pct})
        parts.append(f"{lab}{pct}" if len(lab) == 1 else f"{lab} {pct}")
    if "rgb" in flags:
        label = "RGB fill"
    elif not parts:
        label = "Paper (no ink)"
    else:
        label = " ".join(parts)
    paper = not parts and "rgb" not in flags
    return label, ink_out, paper, ("coating" in flags), hexrgb(fill_preview_rgb(fill, spot_order))


def _spot_slots(names):
    """Ink slot of each spot ink: 5, 6, 7, ... in the order the inks first appear along the bar.

    `names` is already in that order (it is the order of the S1, S2, ... labels), so the first spot
    ink met gets slot 5, the next slot 6, and so on. K, C, M and Y are always slots 1 to 4.
    The order follows the reading direction: a bar read from the other end (because of a corner
    mark) numbers its spot inks from that end."""
    return {n: 5 + i for i, n in enumerate(names)}


def build_candidate(ev, all_rects, shapes, page_w, page_h, cid, marks=(), texts=(), lines=()):
    axis = ev["axis"]
    members = ev["members"]
    mem_ids = {id(r) for r in members}

    bx0 = min(r["x0"] for r in members)
    bx1 = max(r["x1"] for r in members)
    by0 = min(r["y0"] for r in members)
    by1 = max(r["y1"] for r in members)
    top0 = page_h - by1
    horizontal = axis == "x"
    pitch = ev["pitch"]
    along_len_pt = (bx1 - bx0) if horizontal else (by1 - by0)
    cross_len_pt = (by1 - by0) if horizontal else (bx1 - bx0)

    # ---- QR-style corner mark: decides which end of the bar is slot 1 ------------------------
    near = []
    for m in marks:
        if horizontal:
            a_c, c_off = m["cx"] - bx0, (by0 + by1) / 2 - m["cy"]
        else:
            a_c, c_off = by1 - m["cy"], m["cx"] - (bx0 + bx1) / 2
        half = m["size"] / 2
        if abs(c_off) > max(1.0 * MM2PT, 0.3 * cross_len_pt):
            continue                                   # not in line with the bar
        if a_c - half >= along_len_pt - 0.5:
            side, gap = "end", a_c - half - along_len_pt
        elif a_c + half <= 0.5:
            side, gap = "start", -(a_c + half)
        else:
            continue                                   # overlaps the bar
        if gap > max(3 * pitch, 15 * MM2PT):
            continue                                   # too far away to belong to it
        near.append((gap, side, m, c_off))
    near.sort(key=lambda t: t[0])
    both_ends = len({t[1] for t in near}) == 2
    mark = None if (both_ends or not near) else near[0]
    reverse = bool(mark and mark[1] == "end")

    # ---- the bar's own frame: along = reading direction, cross = across the bar ---------------
    def frame(r):
        if horizontal:
            a0, a1 = ((bx1 - r["x1"], bx1 - r["x0"]) if reverse else (r["x0"] - bx0, r["x1"] - bx0))
            c0, c1 = (page_h - r["y1"]) - top0, (page_h - r["y0"]) - top0
        else:
            a0, a1 = ((r["y0"] - by0, r["y1"] - by0) if reverse else (by1 - r["y1"], by1 - r["y0"]))
            c0, c1 = r["x0"] - bx0, r["x1"] - bx0
        return a0, a1, c0, c1

    # the typical patch size is the most common one, not whichever rectangle came first
    def typical(values):
        med = statistics.median(values)
        return statistics.mean(v for v in values if abs(v - med) <= 0.1) if values else med

    pw = typical([r["x1"] - r["x0"] for r in members])
    ph = typical([r["y1"] - r["y0"] for r in members])
    plen, pthick = (pw, ph) if horizontal else (ph, pw)

    # ---- the slot grid ---------------------------------------------------------------------
    # Fit position against slot number with a straight line: the slope is the true pitch and the
    # intercept is where the first slot nominally starts. A patch that is off the grid is dropped and the
    # line is fitted again, so one misplaced patch cannot bend the whole grid.
    pairs = [(int(round(frame(r)[0] / pitch)), frame(r)[0]) for r in members]
    phase = 0.0  # where the first slot nominally starts (normally 0)
    for _ in range(2):
        ks, aa = [q[0] for q in pairs], [q[1] for q in pairs]
        if len(set(ks)) < 2:
            break
        slope, phase = statistics.linear_regression(ks, aa)
        pitch = slope
        keep = [q for q in pairs if abs(q[1] - (phase + q[0] * pitch)) <= GRID_TOL_MM * MM2PT]
        if len(keep) == len(pairs) or len(keep) < 4:
            break
        pairs = keep

    cell_member, off_grid = {}, []
    for r in sorted(members, key=lambda q: frame(q)[0]):
        a0 = frame(r)[0]
        k = int(round((a0 - phase) / pitch))
        if k in cell_member:
            continue
        cell_member[k] = r
        if abs(a0 - phase - k * pitch) > GRID_TOL_MM * MM2PT:
            off_grid.append((k + 1, (a0 - phase - k * pitch) * PT2MM))
    slot_count = max(cell_member) + 1

    # ---- everything else drawn on the bar: marks (and parts of split cells) -------------------
    min_pt = MIN_MARK_MM * MM2PT
    tol_cross = 0.3 * MM2PT
    extras = {}

    def add_extra(kind, r, fill=None):
        a0, a1, c0, c1 = frame(r)
        if a1 - a0 > 1.5 * pitch:
            return                                    # a backdrop that spans several cells
        if min(a1 - a0, c1 - c0) < min_pt:
            return                                    # a hairline (tick, rule) or a speck, not a mark
        cc = (c0 + c1) / 2
        if cc < -tol_cross or cc > cross_len_pt + tol_cross:
            return                                    # not on the bar
        k = int(math.floor(((a0 + a1) / 2 - phase) / pitch))
        if k < 0 or k >= slot_count:
            return
        extras.setdefault(k, []).append({"kind": kind, "a0": a0, "a1": a1, "c0": c0, "c1": c1, "fill": fill, "rect": r})

    seen = {(round(r["x0"], 1), round(r["y0"], 1), round(r["x1"], 1), round(r["y1"], 1)) for r in members}
    for r in all_rects:
        if id(r) in mem_ids:
            continue
        key = (round(r["x0"], 1), round(r["y0"], 1), round(r["x1"], 1), round(r["y1"], 1))
        if key in seen:
            continue
        seen.add(key)
        add_extra("rect", r, r["fill"])
    for sh in shapes:
        add_extra(sh["kind"], sh)

    # split cells: filled rectangles spanning the cell's width are parts of the cell, not marks
    for k, items in extras.items():
        for e in items:
            e["part"] = False
            if e["kind"] == "rect" and e["fill"] is not None:
                inks, flags = decode_fill(e["fill"])
                if (inks or "rgb" in flags) and (e["a1"] - e["a0"]) >= PART_SPAN * min(plen, pitch):
                    e["part"] = True

    # An outline that exactly traces a patch, and text written on a patch, are not markers.
    # Text in a cell that has no patch (for example a label) still is.
    near_tol = GRID_TOL_MM * MM2PT
    for k in list(extras):
        items = extras[k]
        refs = [frame(cell_member[k])] if k in cell_member else []
        refs += [(e["a0"], e["a1"], e["c0"], e["c1"]) for e in items if e["part"]]
        has_patch = bool(refs)
        kept = []
        for e in items:
            if not e["part"]:
                if e["kind"] == "outline" and any(
                        abs(e["a0"] - q[0]) < near_tol and abs(e["a1"] - q[1]) < near_tol
                        and abs(e["c0"] - q[2]) < near_tol and abs(e["c1"] - q[3]) < near_tol for q in refs):
                    continue
                if e["kind"] == "text" and has_patch:
                    continue
            kept.append(e)
        extras[k] = kept

    # ---- line patches (slur and doubling): a cell filled with parallel straight lines -------------
    # Lines are strokes, so they have no width in an outline and the hairline rule above ignores them.
    # A cell with at least 5 equal, evenly spaced lines in one ink is a patch of its own.
    line_patch = {}
    per_cell = {}
    for ln in lines:
        if ln["w"] < 0.05 * MM2PT:
            continue                                   # a hairline: a tick or a rule, not part of a pattern
        bb = {"x0": min(ln["xa"], ln["xb"]), "x1": max(ln["xa"], ln["xb"]),
              "y0": min(ln["ya"], ln["yb"]), "y1": max(ln["ya"], ln["yb"])}
        a0, a1, c0, c1 = frame(bb)
        cc = (c0 + c1) / 2
        if cc < -tol_cross or cc > cross_len_pt + tol_cross:
            continue
        k = int(math.floor(((a0 + a1) / 2 - phase) / pitch))
        if not (0 <= k < slot_count):
            continue
        across = (c1 - c0) > (a1 - a0)                 # the line runs across the bar, not along it
        phys = "vertical" if abs(ln["xa"] - ln["xb"]) < 0.02 else "horizontal"
        ink_sig = tuple(sorted((key, round(t, 3)) for key, t in decode_fill(ln["stroke"])[0]))
        per_cell.setdefault(k, {}).setdefault((phys, across, round(ln["w"] * PT2MM, 2), ink_sig), []).append(
            ((a0 + a1) / 2 if across else cc, ln))
    for k, groups_ in per_cell.items():
        good = []
        for (phys, across, _w, ink_sig), items in groups_.items():
            if len(items) < 5 or not ink_sig:
                continue
            pos = sorted(x[0] for x in items)
            steps = [b2 - a2 for a2, b2 in zip(pos, pos[1:])]
            step = statistics.median(steps)
            width_pt = statistics.median(x[1]["w"] for x in items)
            span = (plen if across else pthick)        # the cell's size in the direction the lines are spread
            if step <= 1.05 * width_pt or any(abs(d - step) > 0.25 * step for d in steps):
                continue                               # not evenly spaced, or the lines would overlap into a solid
            if len(items) * step < 0.6 * span:
                continue                               # fills only a corner of the cell
            good.append((len(items), phys, across, pos, step, width_pt, items[0][1]["stroke"]))
        if len(good) == 1:                             # two directions in one cell would be a cross-hatch: left alone
            n_, phys, across, pos, step, width_pt, stroke = good[0]
            first = (pos[0] - (phase + k * pitch)) if across else pos[0]
            line_patch[k] = {"direction": phys, "count": n_, "width_mm": round(width_pt * PT2MM, 3),
                             "pitch_mm": round(step * PT2MM, 3), "frame_dir": "across" if across else "along",
                             "first_mm": round(first * PT2MM, 3), "fill": stroke}

    # ---- spot colors are numbered by first appearance along the reading direction -------------
    spot_names = []

    def note_spots(fill):
        for key, _t in decode_fill(fill)[0]:
            if key.startswith("spot:") and key[5:] not in spot_names:
                spot_names.append(key[5:])

    for k in range(slot_count):
        ex = extras.get(k, [])
        if any(not e["part"] for e in ex):
            continue                                  # an NU cell carries no ink
        if k in line_patch:
            note_spots(line_patch[k]["fill"])
            continue
        parts = sorted((e for e in ex if e["part"]), key=lambda e: (e["c0"], e["a0"]))
        if parts:
            for e in parts:
                note_spots(e["fill"])
        elif k in cell_member:
            note_spots(cell_member[k]["fill"])
    spot_label = {n: "S%d" % (i + 1) for i, n in enumerate(spot_names)}

    # ---- one record per slot, always ----------------------------------------------------------
    bar_along_mm = along_len_pt * PT2MM

    def place(a0, c0, length, thick):
        """Page-orientation box (relative to the bar's top-left) from reading-frame values, in mm."""
        a_page = (bar_along_mm - (a0 + length)) if reverse else a0
        return ((a_page, c0, length, thick) if horizontal else (c0, a_page, thick, length))

    patches = []
    counts = Counter()
    for k in range(slot_count):
        ex = extras.get(k, [])
        marks_here = [e for e in ex if not e["part"]]
        parts = sorted((e for e in ex if e["part"]), key=lambda e: (e["c0"], e["a0"]))
        m = cell_member.get(k)
        if m is not None:
            _a0, _a1, mc0, mc1 = frame(m)
            c_start, c_thick = mc0, mc1 - mc0
        else:
            c_start, c_thick = 0.0, pthick
        rec = {"slot": k + 1, "along_mm": round(k * pitch * PT2MM, 3), "cross_mm": round(c_start * PT2MM, 3),
               "len_mm": round(plen * PT2MM, 3), "thick_mm": round(c_thick * PT2MM, 3),
               "parts": [], "detail": "", "empty": m is None and not ex,
               "paper": False, "coating": False, "inks": [], "lines": None}
        if marks_here:
            kinds = Counter(e["kind"] for e in marks_here)
            words = {"rect": ("box", "boxes"), "outline": ("outline", "outlines"), "curve": ("curve", "curves"),
                     "shape": ("shape", "shapes"), "text": ("text run", "text runs"), "image": ("image", "images")}
            rec.update(kind="nu", label="NU", rgb=hexrgb(NU_RGB),
                       detail=", ".join(f"{n} {words[kd][0 if n == 1 else 1]}" for kd, n in kinds.most_common()))
        elif k in line_patch:
            lp_ = line_patch[k]
            lab, ink_out, _p, coat, rgb = _describe_fill(lp_["fill"], spot_label, spot_names)
            rec.update(kind="lines", label=f"S/D {lp_['direction']} lines {lab}", inks=ink_out, coating=coat, rgb=rgb,
                       empty=False, lines={x: lp_[x] for x in ("direction", "count", "width_mm", "pitch_mm", "frame_dir", "first_mm")},
                       detail=f"{lp_['count']} lines, {lp_['width_mm']:g} mm wide, {lp_['pitch_mm']:g} mm apart")
        elif len(parts) >= 2:
            labs, outs = [], []
            for e in parts:
                lab, ink_out, _p, _c, rgb = _describe_fill(e["fill"], spot_label, spot_names)
                labs.append(lab)
                outs.append({"label": lab, "rgb": rgb, "inks": ink_out,
                             "along_mm": round((e["a0"] - phase - k * pitch) * PT2MM + k * pitch * PT2MM, 3),
                             "cross_mm": round(e["c0"] * PT2MM, 3),
                             "len_mm": round((e["a1"] - e["a0"]) * PT2MM, 3),
                             "thick_mm": round((e["c1"] - e["c0"]) * PT2MM, 3)})
            rec.update(kind="split", label=" / ".join(labs), rgb="#ffffff", parts=outs,
                       detail=f"{len(parts)} parts across the cell")
        else:
            fill = parts[0]["fill"] if parts else (m["fill"] if m is not None else None)
            if fill is None:
                rec.update(kind="paper", label="Paper (no ink)", rgb="#ffffff", paper=True)
            else:
                lab, ink_out, paper, coat, rgb = _describe_fill(fill, spot_label, spot_names)
                rec.update(kind="paper" if paper else "ink", label=lab, inks=ink_out, paper=paper,
                           coating=coat, rgb=rgb)
        x, y, w, h = place(k * pitch * PT2MM, rec["cross_mm"], rec["len_mm"], rec["thick_mm"])
        rec.update(x_mm=round(x, 3), y_mm=round(y, 3), w_mm=round(w, 3), h_mm=round(h, 3))
        counts[rec["kind"]] += 1
        patches.append(rec)

    bar_w = (bx1 - bx0) * PT2MM
    bar_h = (by1 - by0) * PT2MM
    ink_slots = _spot_slots(spot_names)
    spots = [{"label": spot_label[n], "role": ink_role(n), "pdf_name": n, "ink_slot": ink_slots[n]} for n in spot_names]

    # ---- printed percentage labels against the patches under them ---------------------------------
    # A label such as "75%" printed next to a single-ink patch states that patch's tint. Where the
    # patch is drawn with another tint the file contradicts itself, so the slot is flagged.
    labels_at = {}
    reach = 8 * MM2PT
    for t in texts:
        m_ = re.fullmatch(r"\s*(\d{1,3})\s*%\s*", t["s"])
        if not m_:
            continue
        a, _a1, cpos, _c1 = frame({"x0": t["cx"], "x1": t["cx"], "y0": t["cy"], "y1": t["cy"]})
        if max(0.0, -cpos, cpos - cross_len_pt) > reach:
            continue                                   # not next to the bar
        k = int(math.floor((a - phase) / pitch))
        if 0 <= k < slot_count:
            labels_at.setdefault(k, []).append(int(m_.group(1)))
    mismatches = []
    for k, found_ in labels_at.items():
        rec = patches[k]
        if len(set(found_)) != 1 or rec["kind"] != "ink" or len(rec["inks"]) != 1:
            continue
        want, ink = found_[0], rec["inks"][0]
        if ink["tint"] != want:
            rec["label_pct"] = want
            rec["detail"] = f"label says {want}%"
            mismatches.append({"slot": rec["slot"], "label_pct": want, "ink": ink["ink"], "patch_pct": ink["tint"]})
    mismatches.sort(key=lambda d: d["slot"])

    # ---- warnings ------------------------------------------------------------------------------
    warnings = []
    groups = {}
    for d in mismatches:
        groups.setdefault((d["label_pct"], d["ink"], d["patch_pct"]), []).append(d["slot"])
    for (want, ink, have), sl in groups.items():
        head = [str(x) for x in sl[:8]]
        shown = (" and ".join(head) if len(head) <= 2 else ", ".join(head[:-1]) + " and " + head[-1]) + (" and more" if len(sl) > 8 else "")
        warnings.append(f"The printed label says {want}% but the patch is {ink} {have}% at slot{'s' if len(sl) > 1 else ''} {shown}.")
    for sp in spots:
        if sp["role"] == "coating":
            warnings.append(f"{sp['label']} is a coating plate, not a printing ink. A densitometer usually does not read it, so you may want to leave its patches out of the template.")
        if sp["role"] == "white":
            warnings.append(f"{sp['label']} is a white ink.")
    inked = [p for p in patches if p["kind"] == "ink"]
    solids = [tuple(i["ink"] for i in p["inks"]) for p in inked]
    if (len(patches) >= 4 and inked and len(inked) == len(patches)
            and all(len(p["inks"]) == 1 and p["inks"][0]["tint"] == 100 for p in inked)
            and len(set(solids)) == len(solids)):
        warnings.append("This looks like an ink legend (one 100% swatch per ink), not a measurement bar.")
    if any(p["label"] == "RGB fill" for p in patches):
        warnings.append("These patches are filled with RGB values, not printing inks, so this may not be a measurement bar.")
    if both_ends:
        warnings.append("A corner mark sits at both ends of the bar, so the reading order was left unchanged.")
    if ev["conform"] < 1.0:
        warnings.append("Patch spacing is not perfectly regular; some gaps are not whole patch widths.")
    if off_grid:
        worst = max(off_grid, key=lambda t: abs(t[1]))
        slots_txt = ", ".join(str(k) for k, _d in off_grid[:6]) + (" ..." if len(off_grid) > 6 else "")
        n_off = len(off_grid)
        warnings.append(f"{n_off} patch{'es' if n_off > 1 else ''} (slot {slots_txt}) {'sit' if n_off > 1 else 'sits'} off the {pitch * PT2MM:.3f} mm grid by up to {abs(worst[1]):.3f} mm. Offsets in the table use the grid positions.")
    by_ink = {}
    for p in inked:
        if len(p["inks"]) == 1:
            by_ink.setdefault(p["inks"][0]["ink"], []).append((p["slot"], p["inks"][0]["tint"]))
    for ink, found in by_ink.items():
        odd = [(sl, t) for sl, t in found if t not in STD_TINTS and t != 100]
        if odd:
            others = sorted({t for _sl, t in found if t in STD_TINTS})
            more = f" (and {len(odd) - 1} more)" if len(odd) > 1 else ""
            tail = ("; its other tints are " + ", ".join(f"{t}%" for t in others) + ".") if others else ". Standard tints are 25, 50 and 75%."
            warnings.append(f"{ink} has a {odd[0][1]}% patch at slot {odd[0][0]}{more}{tail}")

    # ---- where reading starts ------------------------------------------------------------------
    if horizontal:
        direction = "right to left" if reverse else "left to right"
        start_pt = ((bx1 - phase) if reverse else (bx0 + phase), top0)
    else:
        direction = "bottom to top" if reverse else "top to bottom"
        start_pt = (bx0, ((page_h - by0) - phase) if reverse else (top0 + phase))

    corner = None
    if mark:
        gap, side, m, c_off = mark
        corner = {
            "found": True,
            "kind": m["kind"],
            "side": side,                     # which end of the default order it was found at
            "applied": reverse,               # True when the bar was read from the mark's end
            "size_mm": round(m["size"] * PT2MM, 3),
            "module_mm": round(m["size"] / 7 * PT2MM, 3),
            "gap_mm": round(gap * PT2MM, 3),  # from the mark to the nearest patch
            "center_mm": {"x": round(m["cx"] * PT2MM, 2), "y": round((page_h - m["cy"]) * PT2MM, 2)},
            "along_mm": round(-(gap + m["size"]) * PT2MM, 3),   # in the reading frame, before slot 1
            "cross_mm": round((cross_len_pt / 2 + c_off - m["size"] / 2) * PT2MM, 3),
        }

    return {
        "id": cid,
        "orientation": "horizontal" if horizontal else "vertical",
        "direction": direction,
        "reference": "corner mark" if mark else "default order",
        "start_mm": {"x": round(start_pt[0] * PT2MM, 2), "y": round(start_pt[1] * PT2MM, 2)},
        "corner_mark": corner,
        "patch_w_mm": round(pw * PT2MM, 4),
        "patch_h_mm": round(ph * PT2MM, 4),
        "pitch_mm": round(pitch * PT2MM, 3),
        "patch_count": slot_count,
        "slot_count": slot_count,
        "counts": {"ink": counts["ink"] + counts["split"], "paper": counts["paper"], "nu": counts["nu"],
                   "sd": counts["lines"]},
        "empty_slots": [p["slot"] for p in patches if p["empty"]],
        "length_mm": round(slot_count * pitch * PT2MM, 3),
        "origin_mm": {"x": round(bx0 * PT2MM, 2), "y": round(top0 * PT2MM, 2)},
        "bbox_mm": {"w": round(bar_w, 3), "h": round(bar_h, 3)},
        "along_len_mm": round((bar_w if horizontal else bar_h), 3),
        "cross_len_mm": round((bar_h if horizontal else bar_w), 3),
        "bbox_pt": {"x0": bx0, "y0": by0, "x1": bx1, "y1": by1},
        "score": ev["score"],
        "spots": spots,
        "patches": patches,
        "warnings": warnings,
        "label_mismatches": mismatches,
        "repeat": None,
        "_member_slots": {id(r): k for k, r in cell_member.items()},   # private, removed by the caller
    }


# --------------------------------------------------------------------------
# Step and repeat
# --------------------------------------------------------------------------
MIN_CORE_SLOTS = 4      # a core design has at least this many slots
MIN_COPIES = 2          # and has to appear at least this many whole times


def _tokens(cand):
    """One signature per slot, in reading order, for comparing bars and finding repeats."""
    return ["nu" if p["kind"] == "nu" else "paper" if p["kind"] == "paper" else p["label"]
            for p in cand["patches"]]


def _default_order(cand):
    """The signatures in the default reading order (left to right, top to bottom)."""
    toks = _tokens(cand)
    return toks[::-1] if cand["direction"] in ("right to left", "bottom to top") else toks


def find_period(tokens, min_period=MIN_CORE_SLOTS, min_copies=MIN_COPIES):
    """The smallest P such that tokens[i] == tokens[i + P] all the way along, with at least `min_copies`
    whole copies of the first P tokens. None when the sequence does not repeat. The last copy may be
    cut short, because a bar ends at its last patch and does not carry the gap that follows."""
    n = len(tokens)
    for period in range(min_period, n // min_copies + 1):
        if all(tokens[i] == tokens[i + period] for i in range(n - period)):
            return period
    return None


def split_segments(ev):
    """Split a line of patches wherever the step to the next patch is not a whole number of pitches.

    Copies stepped by a distance that is not a whole number of patch widths each sit on their own
    grid, so each one becomes its own segment. Segments of fewer than 4 patches are dropped."""
    members, key, base = ev["members"], ("x0" if ev["axis"] == "x" else "top"), ev["pitch"]
    tol = max(0.8, 0.02 * base)
    segs = [[members[0]]]
    for a, b in zip(members, members[1:]):
        step = b[key] - a[key]
        if abs(step - max(1, round(step / base)) * base) > tol:
            segs.append([b])
        else:
            segs[-1].append(b)
    return [sg for sg in segs if len(sg) >= 4]


def _members_box(members):
    return (min(r["x0"] for r in members), min(r["y0"] for r in members),
            max(r["x1"] for r in members), max(r["y1"] for r in members))


def _inside(inner, outer, tol=0.5):
    return (inner[0] >= outer[0] - tol and inner[1] >= outer[1] - tol
            and inner[2] <= outer[2] + tol and inner[3] <= outer[3] + tol)


def build_line_candidates(ev, rects, shapes, page_w, page_h, marks, texts=(), lines=()):
    """Turn one detected line into candidates, folding step-and-repeat copies into a single core.

    Returns a list of candidates (normally one). A folded candidate has "repeat" set and describes
    the core design only."""
    horizontal = ev["axis"] == "x"
    segs = split_segments(ev)
    if not segs:
        return []

    def sub_ev(members):
        return dict(ev, members=members, conform=1.0, score=len(members) + 2 * len({m["sig"] for m in members}))

    built = [build_candidate(sub_ev(sg), rects, shapes, page_w, page_h, 0, marks, texts, lines) for sg in segs]
    seg_start = [(sg[0]["x0"] if horizontal else page_h - sg[0]["y1"]) for sg in segs]

    # --- copies on separate grids: all segments must carry the same design --------------------
    copies_seg, step_seg = 1, None
    core_i = 0
    if len(segs) > 1:
        sigs = [_default_order(b) for b in built]
        same = all(sigs[i] == sigs[0] or (i == len(sigs) - 1 and sigs[0][:len(sigs[i])] == sigs[i])
                   for i in range(1, len(sigs)))
        if same and len(sigs[0]) >= MIN_CORE_SLOTS:
            copies_seg = len(segs)
            steps = [b - a for a, b in zip(seg_start, seg_start[1:])]
            step_seg = statistics.median(steps) * PT2MM
            # read the core the way the bar is meant to be read: from a corner mark when one is in line
            marked = [i for i, b in enumerate(built) if b["reference"] == "corner mark"]
            core_i = marked[0] if marked else 0
        else:
            out = []                                   # different designs on one line: keep them apart
            for sg, b in zip(segs, built):
                b.pop("_member_slots", None)
                b["_rects"] = len(sg)
                out.append(b)
            return out

    core, core_members = built[core_i], segs[core_i]

    # --- copies on the same grid: the slot sequence itself repeats -------------------------------
    n = len(core["patches"])
    period = find_period(_tokens(core))
    copies_grid, partial_last, on_grid, step_grid = 1, False, False, None
    if period:
        slot_of = core["_member_slots"]
        keep = [m for m in core_members if slot_of.get(id(m), 10 ** 9) < period]
        if len(keep) >= MIN_CORE_SLOTS:
            full = core
            core = build_candidate(sub_ev(keep), rects, shapes, page_w, page_h, 0, marks, texts, lines)
            core_slots = core["slot_count"]
            copies_grid = -(-n // period)                           # ceil(n / period)
            partial_last = (n - (copies_grid - 1) * period) < core_slots
            on_grid = True
            step_grid = period * full["pitch_mm"]
    core.pop("_member_slots", None)

    copies = copies_seg * copies_grid
    if copies > 1:
        total_len = ((ev["members"][-1]["x1"] - ev["members"][0]["x0"]) if horizontal
                     else (max(m["y1"] for m in ev["members"]) - min(m["y0"] for m in ev["members"]))) * PT2MM
        core["repeat"] = {
            "found": True,
            "copies_along": copies,
            "copies_across": 1,
            "period_slots": core["slot_count"],
            "core_length_mm": core["length_mm"],
            "full_length_mm": round(total_len, 2),
            "step_mm": round(step_grid if (copies_grid > 1 and copies_seg == 1) else (step_seg if step_seg else step_grid), 3),
            "partial_last": partial_last,
            "on_grid": on_grid and copies_seg == 1,
        }
    core["_rects"] = len(ev["members"])
    return [core]


def _merge_identical_lines(cands):
    """Identical bars on different lines are one design stepped across the sheet: keep the first."""
    out, seen = [], {}
    for c in cands:
        key = (c["orientation"], round(c["patch_w_mm"], 2), round(c["patch_h_mm"], 2), round(c["pitch_mm"], 2),
               tuple(_default_order(c)))
        if key in seen:
            keeper = seen[key]
            rep_ = keeper["repeat"] or {
                "found": True, "copies_along": 1, "copies_across": 1, "period_slots": keeper["slot_count"],
                "core_length_mm": keeper["length_mm"], "full_length_mm": keeper["length_mm"], "step_mm": None,
                "partial_last": False, "on_grid": False}
            rep_["copies_across"] += 1
            keeper["repeat"] = rep_
            keeper["_rects"] += c["_rects"]
            continue
        seen[key] = c
        out.append(c)
    return out


def _open(source):
    if isinstance(source, (bytes, bytearray)):
        return PdfReader(io.BytesIO(bytes(source))), "document.pdf"
    if hasattr(source, "read"):
        return PdfReader(source), getattr(source, "name", "document.pdf")
    return PdfReader(str(source)), os.path.basename(str(source))


def analyze(source, name=None):
    reader, opened_name = _open(source)
    name = name or opened_name
    page = reader.pages[0]
    box = page.cropbox if page.get("/CropBox") is not None else page.mediabox
    llx, lly = float(box.left), float(box.bottom)
    page_w, page_h = float(box.width), float(box.height)
    rot = int(page.get("/Rotate", 0) or 0) % 360

    col = Collector(reader)
    resources = page.get("/Resources")
    resources = resources.get_object() if resources is not None else {}
    contents = page.get_contents()
    ops = contents.operations if contents is not None else []
    col.run(ops, resources, GState())

    rects = []
    seen = set()
    production = 0
    for r in col.rects:
        r = dict(r)
        r["x0"] -= llx
        r["x1"] -= llx
        r["y0"] -= lly
        r["y1"] -= lly
        r["sig"], r["flags"] = fill_signature(r["fill"])
        key = (round(r["x0"], 2), round(r["y0"], 2), round(r["x1"], 2), round(r["y1"], 2), r["sig"])
        if key in seen:
            continue
        seen.add(key)
        if "production" in r["flags"]:
            production += 1
        rects.append(r)

    shapes = []
    for sh in col.shapes:
        sh = dict(sh)
        sh["x0"] -= llx
        sh["x1"] -= llx
        sh["y0"] -= lly
        sh["y1"] -= lly
        shapes.append(sh)
    texts = [dict(t, cx=t["cx"] - llx, cy=t["cy"] - lly) for t in col.texts]
    lines = [dict(l, xa=l["xa"] - llx, xb=l["xb"] - llx, ya=l["ya"] - lly, yb=l["yb"] - lly) for l in col.lines]
    marks = find_corner_marks(rects)
    found = find_bars(rects, page_w, page_h)
    if not found:
        found = find_bars(rects, page_w, page_h, include_rgb=True)

    pending, used, covered = [], [], []
    for ev in found:
        ids = {id(m) for m in ev["members"]}
        if any(len(ids & u) > 0.5 * len(ids) for u in used):
            continue                                   # mostly the same shapes as a better line
        box = _members_box(ev["members"])
        if any(_inside(box, cv) for cv in covered):
            continue                                   # a part of a bar, such as a tint ladder stacked in one cell
        used.append(ids)
        built = build_line_candidates(ev, rects, shapes, page_w, page_h, marks, texts, lines)
        if built:
            covered.append(box)
            pending.extend(built)
        if len(pending) >= 12:
            break
    candidates = _merge_identical_lines(pending)[:4]
    for i, cnd in enumerate(candidates):
        cnd["id"] = i

    bar_shapes = candidates[0].pop("_rects", 0) if candidates else 0
    for cnd in candidates:
        cnd.pop("_rects", None)
        cnd.pop("_member_slots", None)
    ignored = {
        "images": col.stats["images"],
        "text_runs": col.stats["text"],
        "curved_or_irregular_shapes": col.stats["curved_shapes"] + col.stats["other_shapes"],
        "production_ink_rectangles": production,
        "corner_marks": len(marks),
        "other_rectangles": max(0, len(rects) - production - bar_shapes - sum(len(m["rects"]) for m in marks)),
        "repeated_copies": ((candidates[0]["repeat"]["copies_along"] * candidates[0]["repeat"]["copies_across"] - 1)
                            if candidates and candidates[0].get("repeat") else 0),
    }
    result = {
        "file": os.path.basename(str(name)),
        "page": {"w_mm": round(page_w * PT2MM, 2), "h_mm": round(page_h * PT2MM, 2), "rotation": rot},
        "rectangles_found": len(rects),
        "ignored": ignored,
        "candidates": candidates,
    }
    if rot:
        result["note"] = "The page is rotated; orientation is reported in unrotated page coordinates."
    return result



# --------------------------------------------------------------------------
# Output helpers
# --------------------------------------------------------------------------


# --------------------------------------------------------------------------
# XML export, in the format of the colorbar templates
# --------------------------------------------------------------------------
# <ColorBar Name="...">
#   <TemplatePatch Width Height Type="DefaultPatch"/>      the standard patch size
#   <ReferencePatches>  one definition per distinct patch, in order of first use (ID = P_K, P_HC, P_NU ...)
#   <TemplatePatches>   one <TemplatePatch rRef="ID"/> per slot, in reading order
#   <RasterSettings>    the standard raster values
# "Slot" inside a definition is the INK slot (Paper 0, K 1, C 2, M 3, Y 4, spots 5 and up),
# not the position of the patch on the bar.

PROCESS_SLOT = {"K": 1, "C": 2, "M": 3, "Y": 4}
BALANCE = {(50, 40, 40): ("HR", "HighlightRange"), (25, 19, 19): ("HC", "HighlightContrast"),
           (75, 66, 66): ("SC", "ShadowContrast")}
TINT_SNAP = 10          # a tint within this many points of 25, 50 or 75 is written as that tint
RASTER_ATTRS = (("Balance", "0.5 0.4 0.4 0"), ("HighlightContrast_CMY", "0.25 0.19 0.19 0"),
                ("HighlightRange_CMY", "0.5 0.4 0.4 0"), ("ShadowContrast_CMY", "0.75 0.66 0.66 0"))
RASTERS = (("Quarter", "0.25"), ("Half", "0.5"), ("ThreeQuarter", "0.75"),
           ("HighlightContrast_K", "0.25"), ("HighlightRange_K", "0.5"), ("ShadowContrast_K", "0.75"))
_NU_DEF = {"type": "NotUsed", "ptd": {}}


def _g(v):
    return f"{v:g}"


def _num(v):
    return f"{v:.3f}".rstrip("0").rstrip(".") or "0"


def _dim(v):
    """A patch dimension in mm to the nearest 0.0005: 4.0623 is 4.0625, 3.0 is 3, 9.525 stays 9.525."""
    return f"{round(v / 0.0005) * 0.0005:.4f}".rstrip("0").rstrip(".") or "0"


def _xml_patch(p, slot_of, notes, use_labels=True):
    """The template ID and definition for one slot record."""
    kind = p["kind"]
    if kind == "split":
        notes.append(f"Slot {p['slot']} is split into several parts across the cell, so it is written as NU.")
        return "P_NU", _NU_DEF
    if kind == "nu":
        return "P_NU", _NU_DEF
    if kind == "paper":
        return "P_P", {"type": "Paper", "ptd": {"Slot": "0"}}
    inks = sorted(p["inks"], key=lambda i: slot_of[i["ink"]])
    sym = lambda i: i["ink"] if i["ink"] in PROCESS_SLOT else str(slot_of[i["ink"]] - 4)
    if kind == "lines":
        vertical = p["lines"]["direction"] == "vertical"
        return f"P_{'0' if vertical else '90'}\u00b0{sym(inks[0])}", {
            "type": "Slur", "ptd": {"Slot": str(slot_of[inks[0]["ink"]]), "Type": "Vertical" if vertical else "Horizontal"}}
    if len(inks) == 1:
        i = inks[0]
        t, s_, slot = i["tint"], sym(i), slot_of[i["ink"]]
        followed = None
        lp = p.get("label_pct")
        if use_labels and lp is not None and (lp in STD_TINTS or lp == 100) and lp != t:
            followed = (p["slot"], lp, i["ink"], t)
            t = lp
        if t == 100:
            pid, d = f"P_{s_}", {"type": "Solid", "ptd": {"Slot": str(slot)}}
        else:
            near = min(STD_TINTS, key=lambda k: abs(k - t))
            if abs(near - t) <= TINT_SNAP:
                letter, cls = STD_TINTS[near]
                pid = f"P_{letter}{s_}"
                d = {"type": "Tint", "ptd": {"Slot": str(slot), "Raster": _g(near / 100), "RasterClass": str(cls)}}
                if near != t:
                    notes.append(f"Slot {p['slot']}: {i['ink']} {t}% is written as {near}% ({pid}), "
                                 f"because the tints are 25, 50 and 75%.")
            else:
                pid = f"P_{t}{s_}"
                d = {"type": "Tint", "ptd": {"Slot": str(slot), "Raster": _g(t / 100)}}
                notes.append(f"Slot {p['slot']}: {i['ink']} {t}% is not a 25, 50 or 75% tint, so it is written as its "
                             f"own tint ({pid}) without a RasterClass.")
        if followed:
            notes.append(("follow",) + followed + (pid,))      # grouped into one note per kind by build_xml
        return pid, d
    tints = tuple(i["tint"] for i in inks)
    if all(t == 100 for t in tints):
        return "P_" + "".join(sym(i) for i in inks), {
            "type": "Overprint", "ptd": {},
            "inks": [{"Slot": str(slot_of[i["ink"]]), "Type": "Solid"} for i in inks]}
    if tuple(i["ink"] for i in inks) == ("C", "M", "Y") and tints in BALANCE:
        code, gb = BALANCE[tints]
        return f"P_{code}_CMY", {
            "type": "Balance", "ptd": {"GrayBalanceType": gb},
            "inks": [{"Slot": str(slot_of[i["ink"]]), "Raster": _g(i["tint"] / 100), "Type": "Tint"} for i in inks]}
    pid = "P_" + "_".join(f"{sym(i)}{i['tint']}" for i in inks)
    notes.append(f"Slot {p['slot']}: {p['label']} is not a known overprint or gray-balance patch, "
                 f"so it is written as a generic overprint ({pid}).")
    return pid, {"type": "Overprint", "ptd": {}, "inks": [
        ({"Slot": str(slot_of[i["ink"]]), "Type": "Solid"} if i["tint"] == 100 else
         {"Slot": str(slot_of[i["ink"]]), "Raster": _g(i["tint"] / 100), "Type": "Tint"}) for i in inks]}


def build_xml(result, cand, use_labels=True):
    """-> (xml text, notes). Notes list anything that was adjusted or could not be matched exactly.

    use_labels: where a printed percentage label disagrees with the tint drawn in the patch, write the
    label's value (the default) or keep the patch as drawn."""
    notes = []
    slot_of = dict(PROCESS_SLOT)
    for sp in cand["spots"]:
        slot_of[sp["label"]] = sp["ink_slot"]
    ids, defs = [], {}
    for p in cand["patches"]:
        pid, d = _xml_patch(p, slot_of, notes, use_labels)
        ids.append(pid)
        defs.setdefault(pid, d)

    follow, plain = {}, []
    for n in notes:
        if isinstance(n, tuple):
            _, slot, want, ink, have, pid = n
            follow.setdefault((want, ink, have, pid), []).append(slot)
        else:
            plain.append(n)
    for (want, ink, have, pid), sl in follow.items():
        many = len(sl) > 1
        names = [str(x) for x in sl]
        where = " and ".join(names) if len(names) <= 2 else ", ".join(names[:-1]) + " and " + names[-1]
        plain.append(f"Slot{'s' if many else ''} {where}: the printed label says {want}% but the patch is {ink} {have}%, "
                     f"so {'they are' if many else 'it is'} written as {want}%, following the label ({pid}).")
    notes = plain

    stem = os.path.splitext(os.path.basename(str(result.get("file", "") or "ColorBar")))[0]
    root = ET.Element("ColorBar", {"Name": stem.replace("_", " ")})
    ET.SubElement(root, "TemplatePatch", {"Width": _dim(cand["patch_w_mm"]), "Height": _dim(cand["patch_h_mm"]),
                                          "Type": "DefaultPatch"})
    ref = ET.SubElement(root, "ReferencePatches")
    for pid, d in defs.items():
        tp = ET.SubElement(ref, "TemplatePatch", {"Type": d["type"], "ID": pid})
        ptd = ET.SubElement(tp, "PatchTypeData", d["ptd"])
        if d.get("inks"):
            il = ET.SubElement(ptd, "InkList")
            for e in d["inks"]:
                ET.SubElement(il, "PatchTypeData", e)
    seq = ET.SubElement(root, "TemplatePatches")
    for pid in ids:
        ET.SubElement(seq, "TemplatePatch", {"rRef": pid})
    rs = ET.SubElement(root, "RasterSettings", dict(RASTER_ATTRS))
    for t, v in RASTERS:
        ET.SubElement(rs, "Raster", {"Type": t, "Value": v})
    ET.indent(root, space="\t")
    return '<?xml version="1.0"?>\n' + ET.tostring(root, encoding="unicode") + "\n", notes


def candidate_xml(result, cand, use_labels=True):
    return build_xml(result, cand, use_labels)[0]


def analyze_for_web(source, name=None):
    """analyze() plus a ready-to-download XML string on every candidate."""
    res = analyze(source, name)
    res["engine_version"] = __version__
    for c in res["candidates"]:
        c["xml"], c["xml_notes"] = build_xml(res, c)
        if c["label_mismatches"]:                       # offer the other reading as well
            c["xml_as_drawn"], c["xml_as_drawn_notes"] = build_xml(res, c, use_labels=False)
    return res
