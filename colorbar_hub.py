#!/usr/bin/env python3
"""
Command-line wrapper around colorbar_core.

    python colorbar_hub.py sheet.pdf          # readable summary
    python colorbar_hub.py sheet.pdf --json   # full result as JSON
    python colorbar_hub.py sheet.pdf --xml    # the bar as an XML document
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
    print(f"  Slots:      {c['slot_count']}  (ink {n['ink']}, paper white {n['paper']}, NU {n['nu']})")
    print(f"  Length:     {c['length_mm']:.2f} mm")
    m = c.get("corner_mark")
    if m:
        print(f"  Corner mark: {m['kind']} pattern, {m['size_mm']:.2f} mm, {m['gap_mm']:.2f} mm from the bar"
              + (" (bar is read from the patch next to it)" if m["applied"] else " (already at the start of the bar)"))
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
          f"{ig.get('corner_marks', 0)} corner marks, "
          f"{ig['other_rectangles']} other rectangles.")



def main():
    ap = argparse.ArgumentParser(
        description="Find the linear color bar in a press-sheet PDF and report its patch size in mm.")
    ap.add_argument("pdf", help="PDF to inspect")
    ap.add_argument("--json", action="store_true", help="print the full result as JSON")
    ap.add_argument("--xml", action="store_true", help="print the bar as an XML document")
    a = ap.parse_args()
    res = core.analyze(a.pdf)
    if a.json:
        print(json.dumps(res, indent=2))
    elif a.xml:
        if not res["candidates"]:
            sys.exit("No linear color bar found.")
        xml, notes = core.build_xml(res, res["candidates"][0])
        print(xml, end="")
        for n in notes:
            print("note:", n, file=sys.stderr)
    else:
        print_summary(res)


if __name__ == "__main__":
    main()
