#!/usr/bin/env python3
"""
Command-line wrapper around colorbar_core.

    python colorbar_hub.py sheet.pdf          # readable summary
    python colorbar_hub.py sheet.pdf --json   # full result as JSON
    python colorbar_hub.py sheet.pdf --xml    # the bar as an XML document
    python colorbar_hub.py sheet.pdf --stp --title "TECHKON TCS 325 C4" > strip.stp   # a Techkon STP strip
"""
import argparse
import json
import sys

import colorbar_core as core


def print_summary(res):
    print(f"File: {res['file']}   Page: {res['page']['w_mm']} x {res['page']['h_mm']} mm")
    if not res["candidates"]:
        print("No linear color bar found.")
        return
    c = res["candidates"][0]
    vertical = c["orientation"] == "vertical"
    print(f"\nColor bar: {c['orientation']}")
    print(f"  Patch size: {c['patch_w_mm']:.3f} x {c['patch_h_mm']:.3f} mm"
          + ("  (across x along the strip)" if vertical else "  (width x height)"))
    print(f"  Pitch:      {c['pitch_mm']:.3f} mm")
    n = c["counts"]
    print(f"  Slots:      {c['slot_count']}  (ink {n['ink']}, paper white {n['paper']}, NU {n['nu']}, S/D {n.get('sd', 0)})")
    print(f"  Length:     {c['length_mm']:.2f} mm")
    m = c.get("corner_mark")
    if m:
        print(f"  Corner mark: {m['kind']} pattern, {m['size_mm']:.2f} mm, {m['gap_mm']:.2f} mm from the bar"
              + (" (bar is read from the patch next to it)" if m["applied"] else " (already at the start of the bar)"))
    rp = c.get("repeat")
    if rp:
        bits = []
        if rp["copies_along"] > 1:
            bits.append(f"{rp['copies_along']} times along the bar ({rp['full_length_mm']:.1f} mm in all, every {rp['step_mm']:.1f} mm"
                        + (", a whole number of slots" if rp["on_grid"] else "") + (", last copy cut short" if rp["partial_last"] else "") + ")")
        if rp["copies_across"] > 1:
            bits.append(f"on {rp['copies_across']} lines")
        print(f"  Step and repeat: the core ({rp['period_slots']} slots, {rp['core_length_mm']:.1f} mm) appears "
              + " and ".join(bits) + ". Only one core is shown.")
    print(f"  Reading:    {c['direction']} ({c['reference']})")
    print(f"  Starts at:  x {c['start_mm']['x']} mm from left, y {c['start_mm']['y']} mm from top")
    for wmsg in c["warnings"]:
        print(f"  ! {wmsg}")
    print("\n  slot  offset(mm)  w x h (mm)         composition")
    for p in c["patches"]:
        print(f"  {p['slot']:>4}  {p['along_mm']:>9.3f}  {p['w_mm']:.3f} x {p['h_mm']:.3f}   {p['label']}"
              + (f"  [{p['detail']}]" if p["detail"] else ""))
    ig = res["ignored"]
    print(f"\nIgnored: {ig['images']} images, {ig['text_runs']} text runs, "
          f"{ig['curved_or_irregular_shapes']} curved/irregular shapes, "
          f"{ig['production_ink_rectangles']} production-ink rectangles, "
          f"{ig.get('corner_marks', 0)} corner marks, {ig.get('repeated_copies', 0)} repeated copies, "
          f"{ig['other_rectangles']} other rectangles.")



def main():
    ap = argparse.ArgumentParser(
        description="Find the linear color bar in a press-sheet PDF and report its patch size in mm.")
    ap.add_argument("pdf", help="PDF to inspect")
    ap.add_argument("--json", action="store_true", help="print the full result as JSON")
    ap.add_argument("--xml", action="store_true", help="print the bar as an XML document")
    ap.add_argument("--as-drawn", action="store_true",
                    help="with --xml or --stp: keep each patch as drawn where a printed label disagrees with it")
    ap.add_argument("--stp", action="store_true", help="print the bar as a Techkon STP strip file")
    ap.add_argument("--title", help="with --stp: the strip name on the first line (default: TECHKON plus the file name)")
    ap.add_argument("--inkzone", type=float, default=core.STP_INKZONE_MM,
                    help="with --stp: the inkzone width in mm (default %(default)s)")
    a = ap.parse_args()
    res = core.analyze(a.pdf)
    if a.json:
        print(json.dumps(res, indent=2))
    elif a.stp:
        if not res["candidates"]:
            sys.exit("No linear color bar found.")
        text, notes = core.build_stp(res["candidates"][0], a.title or core.default_stp_title(res["file"]),
                                     a.inkzone, use_labels=not a.as_drawn)
        sys.stdout.buffer.write(text.encode("ascii", "replace"))      # exact bytes: CRLF line ends, no final newline
        for n in notes:
            print("note:", n, file=sys.stderr)
    elif a.xml:
        if not res["candidates"]:
            sys.exit("No linear color bar found.")
        xml, notes = core.build_xml(res, res["candidates"][0], use_labels=not a.as_drawn)
        print(xml, end="")
        for n in notes:
            print("note:", n, file=sys.stderr)
    else:
        print_summary(res)


if __name__ == "__main__":
    main()
