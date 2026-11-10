#!/usr/bin/env python3
"""
Builds small synthetic press sheets for the tests and for the website's
"Try the sample" button. No third-party packages are needed.

    python tests/make_sample.py            # writes tests/sample/sample_bar.pdf

The sheet contains one real color bar plus decoys that a naive detector would
trip over: a row of equal squares in a dieline ink, a row of RGB squares,
a registration cross in the "All" separation, a filled panel and some text.

Marker cells (for the NU rules) can be added with markers=(...):
  "circle_gap"      a circle in the empty slot 8        -> NU
  "square_gap"      a small black square in slot 9      -> NU (different size)
  "circle_on_patch" a circle drawn over the patch in slot 3 -> NU
  "text_on_patch"   a text label on the patch in slot 1 -> still an ink patch
  "text_gap"        a text label in the empty slot 9    -> NU
  "hairline"        a zero-width tick on the border between slots 7 and 8 -> ignored
  "labels"          percentage labels printed under the tint patches (K50, S1 25, S1 50, S1 75), all correct
  "label_wrong"     the same labels, and the C100 patch in slot 2 is labelled "75%" -> the file contradicts itself
  "k60"             the K50 patch in slot 7 is drawn at 60% instead -> tint warning, written as 50% in XML
  "sd_pair"         a slur/doubling pair in the empty slots 8 and 9: vertical lines (slot 8) then horizontal
                    lines (slot 9), K 100%, 0.125 mm lines every 0.25 mm, drawn as strokes
  "sd_pair_spot"    the same pair in the first spot ink
  "few_lines"       only four vertical lines in slot 8 -> too few to be a line patch
  "ladder"          the last slot is a white patch with four stacked tint bars (K 2, 3, 4, 5%) over it,
                    like the tint ladder in the TCS C4 template -> one slot, never a separate bar
  "second_spot"     the C100 patch in slot 2 is drawn in a second spot ink, which therefore appears
                    before the first spot ink when the bar is read from the left

Step and repeat: repeat=(n, step_mm) draws the whole bar (marks included) n times along its length,
step_mm apart from start to start, and rows=(m, spacing_mm) draws it on m lines across the sheet.
as_form=True places the copies as one reusable form XObject, the way a step-and-repeat is usually made.
The bar is 72 mm long, so step 72 puts the copies end to end on one grid, step 80 leaves two empty
slots between them, and step 75.5 puts every copy on its own grid.

Optionally a QR-style corner mark (three concentric squares, 7 : 5 : 3) is drawn in line
with the bar, 3 mm past one end or both: corner_mark="end" | "start" | "both".
Two negative cases are available too: "offset" (not in line) and "far" (too far away).
"""
import os
import sys

MM = 72.0 / 25.4

PATCH_ALONG = 4.0   # mm, along the bar
PATCH_ACROSS = 6.0  # mm, across the bar


def _n(v):
    return ("%.4f" % v).rstrip("0").rstrip(".")


def _sep(name, c1, tint="1"):
    return ("[/Separation /%s /DeviceCMYK << /FunctionType 2 /Domain [0 1] "
            "/C0 [0 0 0 0] /C1 [%s] /N 1 >>]" % (name, c1))


def build_pdf(orientation="horizontal", with_bar=True, rgb_decoy=True, corner_mark=None, markers=(),
              repeat=None, rows=None, as_form=False):
    """Return the bytes of a one-page PDF. Sizes are in mm."""
    if orientation == "horizontal":
        page_w, page_h = 160.0, 70.0
    else:
        page_w, page_h = 70.0, 160.0
    n_rep, step = repeat or (1, 0.0)
    n_rows, row_gap = rows or (1, 0.0)
    if orientation == "horizontal":
        page_w += (n_rep - 1) * step
        page_h += (n_rows - 1) * row_gap
    else:
        page_h += (n_rep - 1) * step
        page_w += (n_rows - 1) * row_gap

    c = []  # content stream lines

    def rect(x, y, w, h):
        c.append("%s %s %s %s re f" % (_n(x * MM), _n(y * MM), _n(w * MM), _n(h * MM)))

    def cmyk(*v):
        c.append("%s k" % " ".join(_n(x) for x in v))

    def spot(cs, tint):
        c.append("/%s cs %s scn" % (cs, _n(tint)))

    # --- decoy artwork ----------------------------------------------------
    cmyk(0.08, 0.03, 0, 0)
    rect(8, 28, 30, 22)                      # a plain panel
    cmyk(0, 0.6, 0.9, 0)
    rect(42, 30, 18, 18)                     # another plain shape

    # five equal squares in the dieline ink: would look like a bar if not excluded
    for i in range(5):
        spot("CSdie", 0.2 + 0.2 * i)
        rect(70 + i * 6, 40, 6, 6)

    # a row of RGB squares
    if rgb_decoy:
        for i, rgb in enumerate([(1, 0, 0), (0, 1, 0), (0, 0, 1), (1, 1, 0), (1, 0, 1)]):
            c.append("%s rg" % " ".join(str(x) for x in rgb))
            rect(70 + i * 6, 50, 6, 6)

    # registration cross in "All"
    spot("CSall", 1)
    rect(140, 30, 8, 0.5)
    rect(143.75, 26.25, 0.5, 8)

    # text
    c.append("BT /F1 8 Tf 0 g %s %s Td (SAMPLE SHEET - DECOY ARTWORK) Tj ET" % (_n(8 * MM), _n(60 * MM)))

    def qr_mark(cx, cy, size=3.5):
        """QR finder pattern: 7x7 black square, 5x5 light square, 3x3 black square."""
        m = size / 7.0
        x0, y0, x1, y1 = cx - size / 2, cy - size / 2, cx + size / 2, cy + size / 2
        cmyk(0, 0, 0, 1)
        # the outer square is written as a polygon that starts mid-edge, as real files do
        pts = [(x1, y0 + m), (x1, y0), (x0, y0), (x0, y1), (x1, y1)]
        ops = ["%s %s %s" % (_n(x * MM), _n(y * MM), "m" if i == 0 else "l") for i, (x, y) in enumerate(pts)]
        c.append(" ".join(ops) + " h f")
        cmyk(0, 0, 0, 0)
        rect(cx - 2.5 * m, cy - 2.5 * m, 5 * m, 5 * m)
        cmyk(0, 0, 0, 1)
        rect(cx - 1.5 * m, cy - 1.5 * m, 3 * m, 3 * m)

    def circle(cx, cy, r):
        k = 0.5523 * r
        pts = [(cx + r, cy), (cx + r, cy + k, cx + k, cy + r, cx, cy + r), (cx - k, cy + r, cx - r, cy + k, cx - r, cy),
               (cx - r, cy - k, cx - k, cy - r, cx, cy - r), (cx + k, cy - r, cx + r, cy - k, cx + r, cy)]
        ops = ["%s %s m" % (_n(pts[0][0] * MM), _n(pts[0][1] * MM))]
        for seg in pts[1:]:
            ops.append(" ".join(_n(v * MM) for v in seg) + " c")
        c.append("0 0 0 1 K 0.2 w " + " ".join(ops) + " S")

    # --- the real bar -----------------------------------------------------
    bar_start = len(c)
    if with_bar:
        # (kind, values) per slot; None = empty slot
        group1 = [("k", (0, 0, 0, 1)), ("s2", 1.0) if "second_spot" in markers else ("k", (1, 0, 0, 0)), ("k", (0, 1, 0, 0)), ("k", (0, 0, 1, 0)),
                  ("s", 1.0), ("k", (0.5, 0.4, 0.4, 0)), ("k", (0, 0, 0, 0.6 if "k60" in markers else 0.5)), ("k", (0, 0, 0, 0))]
        group2 = [("k", (0, 0, 0, 1)), ("k", (1, 0, 0, 0)), ("k", (0, 1, 0, 0)), ("k", (0, 0, 1, 0)),
                  ("s", 1.0), ("s", 0.25), ("s", 0.5),
                  ("k", (0, 0, 0, 0)) if "ladder" in markers else ("s", 0.75)]
        slots = group1 + [None, None] + group2
        x0, y0 = 14.0, 8.0  # bar start (left edge / bottom edge) in mm
        for i, s in enumerate(slots):
            if s is None:
                continue
            if s[0] == "k":
                cmyk(*s[1])
            else:
                spot("CSspot2" if s[0] == "s2" else "CSspot", s[1])
            if orientation == "horizontal":
                rect(x0 + i * PATCH_ALONG, y0, PATCH_ALONG, PATCH_ACROSS)
            else:
                # vertical: patches stack from the top down
                rect(8.0, page_h - 14.0 - (i + 1) * PATCH_ALONG, PATCH_ACROSS, PATCH_ALONG)

        def cell_centre(k):                       # centre of slot k (horizontal samples only)
            return x0 + (k + 0.5) * PATCH_ALONG, y0 + PATCH_ACROSS / 2

        if "labels" in markers or "label_wrong" in markers:
            def label(slot_index, text):
                lx = x0 + slot_index * PATCH_ALONG + 0.8
                c.append("BT /F1 3 Tf 0 g %s %s Td (%s) Tj ET" % (_n(lx * MM), _n((y0 - 2.5) * MM), text))
            label(6, "50%")
            label(15, "25%")
            label(16, "50%")
            label(17, "75%")
            if "label_wrong" in markers:
                label(1, "75%")

        def cell_rect(k):                          # slot k as (left, bottom, width, height) in mm
            if orientation == "horizontal":
                return x0 + k * PATCH_ALONG, y0, PATCH_ALONG, PATCH_ACROSS
            top = page_h - 14.0
            return 8.0, top - (k + 1) * PATCH_ALONG, PATCH_ACROSS, PATCH_ALONG

        def lines_patch(k, direction, spot=False, count=None, pitch=0.25, width=0.125):
            cx0, cy0, w, h = cell_rect(k)
            c.append(("/CSspot CS 1 SCN" if spot else "0 0 0 1 K") + " %s w" % _n(width * MM))
            span = w if direction == "vertical" else h
            n = count or int((span - 0.2) / pitch) + 1
            for i in range(n):
                off = 0.1 + i * pitch
                if direction == "vertical":
                    c.append("%s %s m %s %s l S" % (_n((cx0 + off) * MM), _n(cy0 * MM), _n((cx0 + off) * MM), _n((cy0 + h) * MM)))
                else:
                    c.append("%s %s m %s %s l S" % (_n(cx0 * MM), _n((cy0 + off) * MM), _n((cx0 + w) * MM), _n((cy0 + off) * MM)))

        for mk in markers:
            if mk in ("sd_pair", "sd_pair_spot"):
                lines_patch(8, "vertical", spot=mk == "sd_pair_spot")
                lines_patch(9, "horizontal", spot=mk == "sd_pair_spot")
            elif mk == "few_lines":
                lines_patch(8, "vertical", count=4)
            elif mk == "ladder":
                lx = x0 + (len(slots) - 1) * PATCH_ALONG
                for j, tint in enumerate((0.02, 0.03, 0.04, 0.05)):
                    cmyk(0, 0, 0, tint)
                    rect(lx, y0 + PATCH_ACROSS - 1.5 * (j + 1), PATCH_ALONG, 1.5)
            elif mk == "circle_gap":
                circle(*cell_centre(8), 1.5)
            elif mk == "square_gap":
                cx, cy = cell_centre(9)
                cmyk(0, 0, 0, 1)
                rect(cx - 0.7, cy - 0.7, 1.4, 1.4)
            elif mk == "circle_on_patch":
                circle(*cell_centre(3), 1.2)
            elif mk == "text_on_patch":
                cx, cy = cell_centre(1)
                c.append("BT /F1 3 Tf 0 g %s %s Td (C) Tj ET" % (_n((cx - 0.8) * MM), _n((cy - 1) * MM)))
            elif mk == "text_gap":
                cx, cy = cell_centre(9)
                c.append("BT /F1 3 Tf 0 g %s %s Td (AB) Tj ET" % (_n((cx - 1.6) * MM), _n((cy - 1) * MM)))
            elif mk == "hairline":
                c.append("0 0 0 1 K 0 w %s %s m %s %s l S" % (_n((x0 + 8 * PATCH_ALONG) * MM), _n((y0 - 3) * MM),
                                                             _n((x0 + 8 * PATCH_ALONG) * MM), _n((y0 + 9) * MM)))

        if corner_mark:
            n = len(slots)
            half = 1.75
            gap = {"far": 40.0}.get(corner_mark, 3.0)
            off = 4.0 if corner_mark == "offset" else 0.0
            centre = 3.0 + off  # centre line of the bar across its thickness
            ends = []
            if corner_mark in ("end", "both", "offset", "far"):
                ends.append("end")
            if corner_mark in ("start", "both"):
                ends.append("start")
            for e in ends:
                if orientation == "horizontal":
                    a = (x0 + n * PATCH_ALONG + gap + half) if e == "end" else (x0 - gap - half)
                    qr_mark(a, y0 + centre)
                else:
                    top = page_h - 14.0
                    a = (top - n * PATCH_ALONG - gap - half) if e == "end" else (top + gap + half)
                    qr_mark(8.0 + centre, a)

    bar_end = len(c)

    # --- step and repeat: the bar's drawing is replayed at each position -------------------
    form = None
    if with_bar and (n_rep > 1 or n_rows > 1):
        bar_lines = c[bar_start:bar_end]
        del c[bar_start:bar_end]
        form = "\n".join(bar_lines).encode("latin-1") if as_form else None
        for r_ in range(n_rows):
            for k in range(n_rep):
                dx, dy = ((k * step, r_ * row_gap) if orientation == "horizontal" else (r_ * row_gap, k * step))
                c.append("q 1 0 0 1 %s %s cm" % (_n(dx * MM), _n(dy * MM)))
                if as_form:
                    c.append("/Fm1 Do")
                else:
                    c.extend(bar_lines)
                c.append("Q")

    stream = "\n".join(c).encode("latin-1")

    objs = {}
    objs[1] = b"<< /Type /Catalog /Pages 2 0 R >>"
    objs[2] = b"<< /Type /Pages /Kids [3 0 R] /Count 1 >>"
    extra = (" /CSspot2 " + _sep("Sample#20Green", "0.93 0 1 0")) if "second_spot" in markers else ""
    res = ("<< /Font << /F1 5 0 R >> /ColorSpace << /CSdie %s /CSall %s /CSspot %s%s >> >>" % (
        _sep("Dieline", "0 1 1 0"), _sep("All", "1 1 1 1"), _sep("Sample#20Orange", "0 0.52 1 0"), extra))
    page_res = (res[:-3] + " /XObject << /Fm1 6 0 R >> >>") if form is not None else res
    objs[3] = ("<< /Type /Page /Parent 2 0 R /MediaBox [0 0 %s %s] /Resources %s /Contents 4 0 R >>" % (
        _n(page_w * MM), _n(page_h * MM), page_res)).encode("latin-1")
    objs[4] = b"<< /Length %d >>\nstream\n" % len(stream) + stream + b"\nendstream"
    objs[5] = b"<< /Type /Font /Subtype /Type1 /BaseFont /Helvetica >>"
    if form is not None:
        objs[6] = (b"<< /Type /XObject /Subtype /Form /BBox [-5000 -5000 20000 20000] /Resources %s /Length %d >>\nstream\n"
                   % (res.encode("latin-1"), len(form))) + form + b"\nendstream"

    out = bytearray(b"%PDF-1.4\n")
    offsets = {}
    for num in sorted(objs):
        offsets[num] = len(out)
        out += b"%d 0 obj\n" % num + objs[num] + b"\nendobj\n"
    xref = len(out)
    out += b"xref\n0 %d\n" % (len(objs) + 1)
    out += b"0000000000 65535 f \n"
    for num in sorted(objs):
        out += b"%010d 00000 n \n" % offsets[num]
    out += b"trailer\n<< /Size %d /Root 1 0 R >>\nstartxref\n%d\n%%%%EOF\n" % (len(objs) + 1, xref)
    return bytes(out)


if __name__ == "__main__":
    here = os.path.dirname(os.path.abspath(__file__))
    target = os.path.join(here, "sample", "sample_bar.pdf")
    os.makedirs(os.path.dirname(target), exist_ok=True)
    with open(target, "wb") as fh:
        fh.write(build_pdf("horizontal", corner_mark="end", markers=("circle_gap",)))
    print("wrote", target)
    sys.exit(0)
