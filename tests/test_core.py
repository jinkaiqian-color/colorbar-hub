import os
import sys

import pytest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)
sys.path.insert(0, os.path.join(ROOT, "tests"))

import colorbar_core as core  # noqa: E402
from make_sample import build_pdf  # noqa: E402


def approx(v, target, tol=0.005):
    return abs(v - target) <= tol


def bar(**kw):
    return core.analyze(build_pdf(**kw))["candidates"][0]


def labels(c):
    return [p["label"] for p in c["patches"]]


def kinds(c):
    return [p["kind"] for p in c["patches"]]


# ---- the basic bar ----------------------------------------------------------------------------

def test_horizontal_bar_size_and_slots():
    res = core.analyze(build_pdf("horizontal"))
    assert len(res["candidates"]) == 1, "decoys must not become candidates"
    c = res["candidates"][0]
    assert c["orientation"] == "horizontal"
    assert approx(c["patch_w_mm"], 4.0) and approx(c["patch_h_mm"], 6.0) and approx(c["pitch_mm"], 4.0)
    assert c["slot_count"] == 18 and c["patch_count"] == 18
    assert c["counts"] == {"ink": 15, "paper": 3, "nu": 0, "sd": 0}
    assert c["empty_slots"] == [9, 10]                 # slots are numbered from 1
    assert approx(c["origin_mm"]["x"], 14.0, 0.05)
    assert c["warnings"] == []


def test_slot_numbers_start_at_1():
    for kw in ({"orientation": "horizontal"}, {"orientation": "vertical"},
               {"orientation": "horizontal", "corner_mark": "end"}):
        c = bar(**kw)
        assert c["patches"][0]["slot"] == 1 and c["patches"][-1]["slot"] == c["slot_count"]
        assert min(c["empty_slots"]) >= 1


def test_every_slot_has_a_row_and_the_index_never_skips():
    for kw in ({"orientation": "horizontal"}, {"orientation": "vertical"},
               {"orientation": "horizontal", "corner_mark": "end"},
               {"orientation": "horizontal", "markers": ("circle_gap", "square_gap")}):
        c = bar(**kw)
        assert [p["slot"] for p in c["patches"]] == list(range(1, c["slot_count"] + 1))
        assert all(isinstance(p["slot"], int) for p in c["patches"])


def test_paper_white_covers_both_white_patches_and_empty_slots():
    c = bar(orientation="horizontal")
    assert kinds(c)[7] == "paper" and labels(c)[7] == "Paper (no ink)"        # a drawn white patch
    assert kinds(c)[8] == "paper" and labels(c)[8] == "Paper (no ink)"        # nothing drawn
    assert c["patches"][8]["empty"] is True and c["patches"][7]["empty"] is False
    assert labels(c)[:7] == ["K100", "C100", "M100", "Y100", "S1 100", "C50 M40 Y40", "K50"]


def test_nothing_is_called_stacked():
    c = bar(orientation="horizontal", corner_mark="end", markers=("circle_gap",))
    assert all(p["kind"] in ("ink", "paper", "nu", "split") for p in c["patches"])
    assert "stacked" not in " ".join(str(p["slot"]) for p in c["patches"]).lower()


def test_spot_colors_are_numbered_not_named():
    c = bar(orientation="horizontal")
    assert [s["label"] for s in c["spots"]] == ["S1"]
    assert {"S1 100", "S1 25", "S1 75"} <= set(labels(c))


def test_production_ink_and_rgb_decoys_are_ignored():
    res = core.analyze(build_pdf("horizontal"))
    assert res["ignored"]["production_ink_rectangles"] >= 5
    for c in res["candidates"]:
        assert c["origin_mm"]["y"] > 50  # decoys are above this


def test_vertical_bar():
    c = bar(orientation="vertical")
    assert c["orientation"] == "vertical"
    assert approx(c["patch_w_mm"], 6.0) and approx(c["patch_h_mm"], 4.0)
    assert c["slot_count"] == 18 and c["empty_slots"] == [9, 10]


def test_no_bar_found():
    res = core.analyze(build_pdf("horizontal", with_bar=False, rgb_decoy=False))
    assert res["candidates"] == []


def test_rgb_only_row_is_reported_with_a_warning():
    res = core.analyze(build_pdf("horizontal", with_bar=False))
    assert len(res["candidates"]) == 1
    assert any("RGB" in w for w in res["candidates"][0]["warnings"])


def test_rgb_row_is_ignored_when_a_real_bar_exists():
    res = core.analyze(build_pdf("horizontal"))
    assert all(not any("RGB" in w for w in c["warnings"]) for c in res["candidates"])


def test_accepts_bytes_path_and_file_object(tmp_path):
    data = build_pdf("horizontal")
    p = tmp_path / "x.pdf"
    p.write_bytes(data)
    a = core.analyze(data)["candidates"][0]["slot_count"]
    b = core.analyze(str(p))["candidates"][0]["slot_count"]
    with open(p, "rb") as fh:
        c = core.analyze(fh)["candidates"][0]["slot_count"]
    assert a == b == c == 18


def test_web_result_has_xml_and_no_csv():
    res = core.analyze_for_web(build_pdf("horizontal", markers=("circle_gap",)), name="Sample Sheet_A.pdf")
    c = res["candidates"][0]
    assert c["xml"].startswith('<?xml version="1.0"?>\n<ColorBar Name="Sample Sheet A">')
    assert "csv" not in c and not hasattr(core, "candidate_csv")
    assert isinstance(c["xml_notes"], list)


def test_xml_follows_the_template_layout():
    import xml.etree.ElementTree as ET
    res = core.analyze_for_web(build_pdf("horizontal"), name="sample_bar.pdf")
    text = res["candidates"][0]["xml"]
    assert text.endswith("</ColorBar>\n") and "\n\t<TemplatePatch Width=\"4\" Height=\"6\" Type=\"DefaultPatch\" />\n" in text
    root = ET.fromstring(text)
    assert [ch.tag for ch in root] == ["TemplatePatch", "ReferencePatches", "TemplatePatches", "RasterSettings"]
    seq = [t.get("rRef") for t in root.find("TemplatePatches")]
    assert len(seq) == 18
    defs = [t.get("ID") for t in root.find("ReferencePatches")]
    first = []
    for i in seq:
        if i not in first:
            first.append(i)
    assert defs == first                                      # definitions in order of first use
    assert set(seq) == set(defs)
    assert root.find("RasterSettings").get("HighlightContrast_CMY") == "0.25 0.19 0.19 0"
    assert [r.get("Type") for r in root.find("RasterSettings")][:3] == ["Quarter", "Half", "ThreeQuarter"]


def test_xml_ids_and_ink_slots():
    import xml.etree.ElementTree as ET
    res = core.analyze_for_web(build_pdf("horizontal"))
    root = ET.fromstring(res["candidates"][0]["xml"])
    seq = [t.get("rRef") for t in root.find("TemplatePatches")]
    assert seq == ["P_K", "P_C", "P_M", "P_Y", "P_1", "P_HR_CMY", "P_HK", "P_P", "P_P", "P_P",
                   "P_K", "P_C", "P_M", "P_Y", "P_1", "P_Q1", "P_H1", "P_T1"]
    d = {t.get("ID"): t for t in root.find("ReferencePatches")}
    ptd = lambda i: d[i].find("PatchTypeData").attrib
    assert (d["P_K"].get("Type"), ptd("P_K")) == ("Solid", {"Slot": "1"})
    assert ptd("P_C") == {"Slot": "2"} and ptd("P_M") == {"Slot": "3"} and ptd("P_Y") == {"Slot": "4"}
    assert ptd("P_1") == {"Slot": "5"}                                    # first spot ink is slot 5
    assert ptd("P_Q1") == {"Slot": "5", "Raster": "0.25", "RasterClass": "1"}
    assert ptd("P_H1") == {"Slot": "5", "Raster": "0.5", "RasterClass": "2"}
    assert ptd("P_T1") == {"Slot": "5", "Raster": "0.75", "RasterClass": "3"}
    assert (d["P_P"].get("Type"), ptd("P_P")) == ("Paper", {"Slot": "0"})
    bal = d["P_HR_CMY"]
    assert bal.get("Type") == "Balance" and bal.find("PatchTypeData").get("GrayBalanceType") == "HighlightRange"
    assert [(e.get("Slot"), e.get("Raster"), e.get("Type")) for e in bal.iter("PatchTypeData") if e.get("Slot")] == [
        ("2", "0.5", "Tint"), ("3", "0.4", "Tint"), ("4", "0.4", "Tint")]


def test_xml_nu_slot_is_written_as_not_used():
    res = core.analyze_for_web(build_pdf("horizontal", markers=("circle_gap",)))
    text = res["candidates"][0]["xml"]
    assert '<TemplatePatch Type="NotUsed" ID="P_NU">\n\t\t\t<PatchTypeData />\n\t\t</TemplatePatch>' in text
    assert text.count('rRef="P_NU"') == 1


def test_spot_ink_slots_follow_the_order_the_inks_appear_in_the_bar():
    assert core._spot_slots(["A", "B", "C"]) == {"A": 5, "B": 6, "C": 7}
    assert core._spot_slots([]) == {}
    # two spot inks: the second one is drawn first (slot 2) when the bar is read from the left
    c = core.analyze_for_web(build_pdf("horizontal", markers=("second_spot",)))["candidates"][0]
    assert [(s["label"], s["pdf_name"], s["ink_slot"]) for s in c["spots"]] == [
        ("S1", "Sample Green", 5), ("S2", "Sample Orange", 6)]
    assert c["patches"][1]["label"] == "S1 100"                     # the Green patch, label S1
    import xml.etree.ElementTree as ET
    root = ET.fromstring(c["xml"])
    d = {t.get("ID"): t.find("PatchTypeData").attrib for t in root.find("ReferencePatches")}
    assert d["P_1"] == {"Slot": "5"} and d["P_2"] == {"Slot": "6"}
    assert [t.get("rRef") for t in root.find("TemplatePatches")][:5] == ["P_K", "P_1", "P_M", "P_Y", "P_2"]


def test_a_bar_read_from_the_other_end_numbers_its_spot_inks_from_that_end():
    # read from the right (corner mark at the right end) the Orange patches come first
    c = core.analyze_for_web(build_pdf("horizontal", corner_mark="end", markers=("second_spot",)))["candidates"][0]
    assert [(s["label"], s["pdf_name"], s["ink_slot"]) for s in c["spots"]] == [
        ("S1", "Sample Orange", 5), ("S2", "Sample Green", 6)]


def test_a_single_spot_ink_is_slot_5_in_either_direction():
    for kw in ({}, {"corner_mark": "end"}):
        c = core.analyze_for_web(build_pdf("horizontal", **kw))["candidates"][0]
        assert [(s["label"], s["ink_slot"]) for s in c["spots"]] == [("S1", 5)]


def test_a_tint_between_the_standard_ones_is_warned_about_and_written_as_the_nearest():
    import xml.etree.ElementTree as ET
    res = core.analyze_for_web(build_pdf("horizontal", markers=("k60",)))
    c = res["candidates"][0]
    assert any(w.startswith("K has a 60% patch at slot 7") for w in c["warnings"])
    assert any("K 60% is written as 50% (P_HK)" in n for n in c["xml_notes"])
    root = ET.fromstring(c["xml"])
    assert [t.get("rRef") for t in root.find("TemplatePatches")][6] == "P_HK"


def test_standard_tints_raise_no_warning():
    c = bar(orientation="horizontal")
    assert c["warnings"] == []
    assert core.analyze_for_web(build_pdf("horizontal"))["candidates"][0]["xml_notes"] == []


def fake(slot, kind, inks=(), label=None):
    return {"slot": slot, "kind": kind, "label": label or " ".join(f"{i[0]}{i[1]}" for i in inks) or kind,
            "inks": [{"ink": n, "tint": t} for n, t in inks], "parts": [], "detail": ""}


def test_overprints_balances_and_odd_combinations():
    import xml.etree.ElementTree as ET
    patches = [fake(1, "ink", [("C", 100), ("M", 100)]), fake(2, "ink", [("C", 100), ("Y", 100)]),
               fake(3, "ink", [("M", 100), ("Y", 100)]), fake(4, "ink", [("C", 100), ("M", 100), ("Y", 100)]),
               fake(5, "ink", [("C", 25), ("M", 19), ("Y", 19)]), fake(6, "ink", [("C", 75), ("M", 66), ("Y", 66)]),
               fake(7, "ink", [("C", 30), ("M", 20), ("Y", 20)]), fake(8, "nu", label="NU"),
               {**fake(9, "split", [], "K2 / K3"), "parts": [{}]}]
    cand = {"patches": patches, "patch_w_mm": 3.0, "patch_h_mm": 5.0, "spots": []}
    xml, notes = core.build_xml({"file": "x.pdf"}, cand)
    root = ET.fromstring(xml)
    assert [t.get("rRef") for t in root.find("TemplatePatches")] == [
        "P_CM", "P_CY", "P_MY", "P_CMY", "P_HC_CMY", "P_SC_CMY", "P_C30_M20_Y20", "P_NU", "P_NU"]
    d = {t.get("ID"): t for t in root.find("ReferencePatches")}
    assert d["P_CM"].get("Type") == "Overprint"
    assert [e.get("Slot") for e in d["P_CY"].iter("PatchTypeData") if e.get("Slot")] == ["2", "4"]
    assert d["P_HC_CMY"].find("PatchTypeData").get("GrayBalanceType") == "HighlightContrast"
    assert d["P_SC_CMY"].find("PatchTypeData").get("GrayBalanceType") == "ShadowContrast"
    assert len(notes) == 2 and any("generic overprint" in n for n in notes) and any("written as NU" in n for n in notes)


def test_the_given_file_name_is_used_in_the_result_and_the_xml():
    import xml.etree.ElementTree as ET
    res = core.analyze_for_web(build_pdf("horizontal"), name="my sheet & more.pdf")
    assert res["file"] == "my sheet & more.pdf"
    assert ET.fromstring(res["candidates"][0]["xml"]).get("Name") == "my sheet & more"        # escaped correctly


def test_xml_shows_spot_numbers_only_never_ink_names():
    xml = core.analyze_for_web(build_pdf("horizontal"))["candidates"][0]["xml"]
    assert "Sample" not in xml and "Orange" not in xml                                        # the PDF's ink name


def test_committed_sample_matches_generator():
    path = os.path.join(ROOT, "tests", "sample", "sample_bar.pdf")
    if not os.path.exists(path):
        pytest.skip("sample not generated yet")
    assert open(path, "rb").read() == build_pdf("horizontal", corner_mark="end", markers=("circle_gap",))


# ---- NU (not used) cells -----------------------------------------------------------------------

def test_a_marker_in_an_empty_slot_is_nu_and_its_neighbour_stays_paper():
    c = bar(orientation="horizontal", markers=("circle_gap",))
    assert kinds(c)[8] == "nu" and labels(c)[8] == "NU"
    assert "curve" in c["patches"][8]["detail"]
    assert kinds(c)[9] == "paper"
    assert c["counts"] == {"ink": 15, "paper": 2, "nu": 1, "sd": 0}


def test_a_marker_of_a_different_size_is_nu_not_paper():
    c = bar(orientation="horizontal", markers=("square_gap",))
    assert kinds(c)[9] == "nu" and labels(c)[9] == "NU"
    assert kinds(c)[8] == "paper"


def test_a_marker_over_a_patch_makes_the_cell_nu():
    c = bar(orientation="horizontal", markers=("circle_on_patch",))
    assert kinds(c)[3] == "nu"
    assert kinds(c)[2] == "ink" and kinds(c)[4] == "ink"
    # the patch under the marker carries no ink as far as the spot numbering is concerned
    assert c["counts"]["nu"] == 1


def test_text_written_on_a_patch_is_a_label_not_a_marker():
    c = bar(orientation="horizontal", markers=("text_on_patch",))
    assert kinds(c)[1] == "ink" and labels(c)[1] == "C100"
    assert c["counts"]["nu"] == 0


def test_text_in_a_cell_without_a_patch_is_a_marker():
    c = bar(orientation="horizontal", markers=("text_gap",))
    assert kinds(c)[9] == "nu" and "text" in c["patches"][9]["detail"]


def test_a_zero_width_tick_between_cells_is_ignored():
    c = bar(orientation="horizontal", markers=("hairline",))
    assert c["counts"]["nu"] == 0
    assert kinds(c)[7] == "paper" and kinds(c)[8] == "paper"


def test_nu_cells_take_part_in_the_grid_but_not_in_spot_numbering():
    c = bar(orientation="horizontal", markers=("circle_on_patch",))
    assert [s["label"] for s in c["spots"]] == ["S1"]
    assert c["patches"][3]["inks"] == []


# ---- QR-style corner mark: the patch next to it is the first patch ------------------------------

def test_without_a_mark_the_default_order_is_kept():
    c = bar(orientation="horizontal")
    assert c["reference"] == "default order" and c["direction"] == "left to right"
    assert c["corner_mark"] is None
    assert labels(c)[0] == "K100" and labels(c)[-1] == "S1 75"


def test_mark_past_the_end_makes_the_patch_next_to_it_the_first_patch():
    c = bar(orientation="horizontal", corner_mark="end")
    assert c["reference"] == "corner mark" and c["direction"] == "right to left"
    assert labels(c)[0] == "S1 75" and labels(c)[-1] == "K100"
    m = c["corner_mark"]
    assert m["found"] and m["applied"] and m["kind"] == "finder" and m["side"] == "end"
    assert approx(m["size_mm"], 3.5, 0.02) and approx(m["module_mm"], 0.5, 0.01) and approx(m["gap_mm"], 3.0, 0.02)
    assert approx(m["along_mm"], -6.5, 0.03)
    assert approx(c["start_mm"]["x"], 86.0, 0.05)
    assert c["patches"][0]["slot"] == 1 and approx(c["patches"][0]["along_mm"], 0.0)
    assert c["empty_slots"] == [9, 10] and c["slot_count"] == 18


def test_marker_slots_follow_the_reading_order_when_the_bar_is_reversed():
    c = bar(orientation="horizontal", corner_mark="end", markers=("circle_gap",))
    # the circle sits in slot 8 counted from the left; read from the right that is slot 17 - 8 = 9
    assert kinds(c)[9] == "nu" and kinds(c)[8] == "paper"


def test_mark_before_the_start_keeps_the_order_and_is_reported():
    c = bar(orientation="horizontal", corner_mark="start")
    assert c["direction"] == "left to right" and c["reference"] == "corner mark"
    assert labels(c)[0] == "K100"
    assert c["corner_mark"]["side"] == "start" and c["corner_mark"]["applied"] is False
    assert approx(c["start_mm"]["x"], 14.0, 0.05)


def test_vertical_bar_with_a_mark_at_the_bottom_is_read_bottom_to_top():
    c = bar(orientation="vertical", corner_mark="end")
    assert c["direction"] == "bottom to top" and c["reference"] == "corner mark"
    assert labels(c)[0] == "S1 75" and labels(c)[-1] == "K100"
    assert approx(c["start_mm"]["y"], 160.0 - 74.0, 0.05)
    assert approx(c["patch_w_mm"], 6.0) and approx(c["patch_h_mm"], 4.0)


def test_vertical_bar_with_a_mark_at_the_top_keeps_top_to_bottom():
    c = bar(orientation="vertical", corner_mark="start")
    assert c["direction"] == "top to bottom" and labels(c)[0] == "K100"


def test_a_mark_that_is_not_in_line_with_the_bar_is_ignored():
    c = bar(orientation="horizontal", corner_mark="offset")
    assert c["reference"] == "default order" and c["corner_mark"] is None
    assert labels(c)[0] == "K100"


def test_a_mark_too_far_from_the_bar_is_ignored():
    c = bar(orientation="horizontal", corner_mark="far")
    assert c["reference"] == "default order" and c["corner_mark"] is None


def test_marks_at_both_ends_leave_the_order_alone_and_warn():
    c = bar(orientation="horizontal", corner_mark="both")
    assert c["reference"] == "default order" and labels(c)[0] == "K100"
    assert any("both ends" in w for w in c["warnings"])


def test_mark_alone_is_not_a_bar_and_is_counted_as_ignored():
    res = core.analyze(build_pdf("horizontal", with_bar=False, rgb_decoy=False, corner_mark="end"))
    assert res["candidates"] == []
    res = core.analyze(build_pdf("horizontal", corner_mark="end"))
    assert res["ignored"]["corner_marks"] == 1


def test_a_rectangle_written_as_a_polygon_with_extra_points_is_still_a_rectangle():
    poly = [(10, 3), (10, 0), (0, 0), (0, 10), (10, 10)]
    assert len(core._simplify_polygon(poly)) == 4
    closed = [(0, 0), (5, 0), (10, 0), (10, 10), (0, 10), (0, 0)]
    assert len(core._simplify_polygon(closed)) == 4
    assert len(core._simplify_polygon([(0, 0), (10, 0), (5, 8)])) == 3


def test_mark_detector_needs_the_7_5_3_proportions():
    def sq(cx, cy, s):
        return {"x0": cx - s / 2, "x1": cx + s / 2, "y0": cy - s / 2, "y1": cy + s / 2}
    assert len(core.find_corner_marks([sq(50, 50, 10), sq(50, 50, 7.1), sq(50, 50, 4.3)])) == 1
    assert core.find_corner_marks([sq(50, 50, 10), sq(50, 50, 9), sq(50, 50, 8)]) == []
    marks = core.find_corner_marks([sq(50, 50, 10), sq(53, 50, 7.1), sq(50, 50, 4.3)])
    assert len(marks) == 1 and marks[0]["kind"] == "partial"
    assert core.find_corner_marks([sq(50, 50, 10), sq(53, 50, 7.1), sq(53, 50, 4.3)]) == []


# ---- step and repeat ------------------------------------------------------------------------------

def one(**kw):
    return core.analyze_for_web(build_pdf(**kw), name="sheet.pdf")["candidates"][0]


def test_find_period_on_plain_sequences():
    core_ = list("ABCDEFGH")
    assert core.find_period(core_ * 3) == 8
    assert core.find_period(core_ * 2) == 8
    assert core.find_period(core_) is None                     # one copy is not a repeat
    assert core.find_period(core_ * 2 + list("ABC")) == 8      # the last copy may be cut short
    assert core.find_period(list("ABCDEFGHABCDEFGX")) is None  # one slot differs
    assert core.find_period(list("AB") * 6) == 4               # a core has at least 4 slots, so "ABAB" is the core here
    assert core.find_period(list("ABCD") * 3) == 4
    assert core.find_period(list("ABCDXXABCDXXABCD")) == 6     # a gap between the copies is part of the period
    assert core.find_period([]) is None


def test_a_bar_that_does_not_repeat_is_not_flagged():
    c = one(orientation="horizontal")
    assert c["repeat"] is None
    assert core.analyze(build_pdf("horizontal", corner_mark="end", markers=("circle_gap",)))["ignored"]["repeated_copies"] == 0


def test_copies_end_to_end_on_one_grid_fold_into_one_core():
    single = one(orientation="horizontal")
    c = one(orientation="horizontal", repeat=(3, 72.0))
    assert c["slot_count"] == single["slot_count"] == 18
    assert labels(c) == labels(single)
    r = c["repeat"]
    assert r["found"] and r["copies_along"] == 3 and r["copies_across"] == 1 and r["on_grid"] is True
    assert r["period_slots"] == 18 and approx(r["step_mm"], 72.0, 0.05) and approx(r["full_length_mm"], 216.0, 0.05)
    assert approx(r["core_length_mm"], 72.0, 0.05) and r["partial_last"] is False
    assert c["warnings"] == []


def test_copies_with_empty_slots_between_them_fold_and_the_gap_is_not_part_of_the_core():
    c = one(orientation="horizontal", repeat=(3, 80.0))        # two empty slots between the copies
    assert c["slot_count"] == 18 and c["repeat"]["copies_along"] == 3 and c["repeat"]["on_grid"] is True
    assert approx(c["repeat"]["step_mm"], 80.0, 0.05)
    assert c["patches"][-1]["empty"] is False                  # the core ends at its last patch


def test_copies_stepped_off_the_grid_each_get_their_own_grid_and_fold():
    c = one(orientation="horizontal", repeat=(3, 75.5))        # 3.5 mm between the copies: not a whole slot
    assert c["slot_count"] == 18 and c["repeat"]["copies_along"] == 3 and c["repeat"]["on_grid"] is False
    assert approx(c["repeat"]["step_mm"], 75.5, 0.05) and approx(c["repeat"]["full_length_mm"], 223.0, 0.05)
    assert c["warnings"] == []                                 # no "off the grid" or "irregular spacing" warnings
    assert labels(c) == labels(one(orientation="horizontal"))


def test_copies_placed_as_a_form_xobject_are_found_too():
    for step in (72.0, 75.5):
        c = one(orientation="horizontal", repeat=(3, step), as_form=True)
        assert c["slot_count"] == 18 and c["repeat"]["copies_along"] == 3


def test_the_xml_of_a_repeated_bar_is_the_xml_of_its_core():
    single = core.analyze_for_web(build_pdf("horizontal"), name="sheet.pdf")["candidates"][0]["xml"]
    for kw in ({"repeat": (3, 72.0)}, {"repeat": (3, 80.0)}, {"repeat": (4, 75.5)}, {"repeat": (3, 75.5), "as_form": True},
               {"rows": (2, 9.0)}, {"repeat": (3, 75.5), "rows": (2, 9.0)}):
        assert one(orientation="horizontal", **kw)["xml"] == single, kw


def test_identical_bars_on_separate_lines_are_one_design_repeated_across_the_sheet():
    res = core.analyze(build_pdf("horizontal", rows=(2, 9.0)))
    assert len(res["candidates"]) == 1
    r = res["candidates"][0]["repeat"]
    assert r["copies_across"] == 2 and r["copies_along"] == 1 and r["step_mm"] is None
    assert res["ignored"]["repeated_copies"] == 1


def test_repeats_along_and_across_together():
    r = one(orientation="horizontal", repeat=(3, 75.5), rows=(2, 9.0))["repeat"]
    assert r["copies_along"] == 3 and r["copies_across"] == 2


def test_vertical_bars_repeat_too():
    for step, on_grid in ((72.0, True), (75.5, False)):
        c = one(orientation="vertical", repeat=(3, step))
        assert c["orientation"] == "vertical" and c["slot_count"] == 18
        assert c["repeat"]["copies_along"] == 3 and c["repeat"]["on_grid"] is on_grid


def test_the_reported_position_and_crop_cover_the_core_only():
    c = one(orientation="horizontal", repeat=(3, 75.5))
    assert approx(c["origin_mm"]["x"], 14.0, 0.05)             # the first copy
    assert approx(c["bbox_mm"]["w"], 72.0, 0.05)               # one core wide, not the whole run
    b = c["bbox_pt"]
    assert approx((b["x1"] - b["x0"]) * 25.4 / 72, 72.0, 0.05)


def test_corner_marks_still_set_the_reading_order_of_a_repeated_bar():
    single = one(orientation="horizontal", corner_mark="end")
    c = one(orientation="horizontal", corner_mark="end", repeat=(3, 90.0))
    assert c["direction"] == single["direction"] == "right to left"
    assert labels(c) == labels(single) and c["repeat"]["copies_along"] == 3


def test_ignored_counts_report_the_extra_copies():
    res = core.analyze(build_pdf("horizontal", repeat=(4, 75.5)))
    assert res["ignored"]["repeated_copies"] == 3
    assert res["ignored"]["other_rectangles"] < 30             # the copies are not counted as stray rectangles


def test_a_tint_ladder_stacked_in_a_cell_is_part_of_the_bar_not_a_bar_of_its_own():
    res = core.analyze(build_pdf("horizontal", markers=("ladder",)))
    assert len(res["candidates"]) == 1
    last = res["candidates"][0]["patches"][-1]
    assert last["kind"] == "split" and last["label"] == "K2 / K3 / K4 / K5"
    res = core.analyze(build_pdf("horizontal", markers=("ladder",), repeat=(3, 75.5)))
    assert len(res["candidates"]) == 1 and res["candidates"][0]["repeat"]["copies_along"] == 3


def test_different_designs_on_one_line_are_not_folded():
    # two grids on one line with different content: the second copy has its S1 tints changed
    toks_a, toks_b = list("ABCDEFGH"), list("ABCDEFGX")
    assert core.find_period(toks_a + toks_b) is None


# ---- printed percentage labels against the patches ---------------------------------------------------

def test_correct_percentage_labels_raise_nothing():
    c = one(orientation="horizontal", markers=("labels",))
    assert c["label_mismatches"] == [] and c["warnings"] == []
    assert "xml_as_drawn" not in c


def test_a_label_that_contradicts_its_patch_is_flagged():
    c = one(orientation="horizontal", markers=("label_wrong",))
    assert c["label_mismatches"] == [{"slot": 2, "label_pct": 75, "ink": "C", "patch_pct": 100}]
    assert c["warnings"] == ["The printed label says 75% but the patch is C 100% at slot 2."]
    rec = c["patches"][1]
    assert rec["label"] == "C100" and rec["label_pct"] == 75 and rec["detail"] == "label says 75%"
    assert c["patches"][0].get("label_pct") is None


def test_the_xml_follows_the_label_by_default_and_can_keep_the_patch_as_drawn():
    import xml.etree.ElementTree as ET
    c = one(orientation="horizontal", markers=("label_wrong",))
    seq = lambda x: [t.get("rRef") for t in ET.fromstring(x).find("TemplatePatches")]
    assert seq(c["xml"])[1] == "P_TC" and seq(c["xml_as_drawn"])[1] == "P_C"
    assert [i for i, (a, b) in enumerate(zip(seq(c["xml"]), seq(c["xml_as_drawn"]))) if a != b] == [1]
    assert any("label says 75% but the patch is C 100%" in n and "(P_TC)" in n for n in c["xml_notes"])
    assert c["xml_as_drawn_notes"] == []
    root = ET.fromstring(c["xml"])
    d = {t.get("ID"): t.find("PatchTypeData").attrib for t in root.find("ReferencePatches")}
    assert d["P_TC"] == {"Slot": "2", "Raster": "0.75", "RasterClass": "3"}
    assert core.build_xml({"file": "sheet.pdf"}, c, use_labels=False)[0] == c["xml_as_drawn"]


def test_labels_are_matched_to_the_right_slot_when_the_bar_is_read_from_the_other_end():
    c = one(orientation="horizontal", corner_mark="end", markers=("label_wrong",))
    assert c["direction"] == "right to left"
    assert c["label_mismatches"][0]["slot"] == 17                        # slot 2 from the left is slot 17 from the right
    assert c["patches"][16]["label_pct"] == 75


def test_a_repeated_bar_reports_a_wrong_label_once_for_the_core():
    for kw in ({"repeat": (3, 72.0)}, {"repeat": (3, 75.5)}, {"repeat": (3, 75.5), "as_form": True}):
        c = one(orientation="horizontal", markers=("label_wrong",), **kw)
        assert [d["slot"] for d in c["label_mismatches"]] == [2], kw


def test_text_that_is_not_a_percentage_or_not_next_to_the_bar_is_ignored():
    # the decoy artwork carries "SAMPLE SHEET - DECOY ARTWORK" far from the bar
    c = one(orientation="horizontal")
    assert c["label_mismatches"] == []


def test_a_label_over_a_patch_with_several_inks_is_not_compared():
    # the gray-balance patch in slot 6 carries three inks; a percentage label there cannot be checked against one tint
    c = one(orientation="horizontal", markers=("labels",))
    assert c["patches"][5]["kind"] == "ink" and len(c["patches"][5]["inks"]) == 3
    assert c["patches"][5].get("label_pct") is None


# ---- slur / doubling (line) patches ------------------------------------------------------------------

def test_a_cell_of_parallel_lines_is_an_sd_patch_not_paper_white():
    c = one(orientation="horizontal", markers=("sd_pair",))
    v, h = c["patches"][8], c["patches"][9]
    assert (v["slot"], v["kind"], v["label"]) == (9, "lines", "S/D vertical lines K100")
    assert (h["slot"], h["kind"], h["label"]) == (10, "lines", "S/D horizontal lines K100")
    assert v["lines"]["direction"] == "vertical" and v["lines"]["count"] == 16
    assert h["lines"]["direction"] == "horizontal" and h["lines"]["count"] == 24
    assert approx(v["lines"]["width_mm"], 0.125, 0.002) and approx(v["lines"]["pitch_mm"], 0.25, 0.005)
    assert v["inks"] == [{"ink": "K", "tint": 100}] and v["empty"] is False and v["paper"] is False
    assert c["counts"] == {"ink": 15, "paper": 1, "nu": 0, "sd": 2}
    assert c["empty_slots"] == []


def test_too_few_lines_are_not_a_patch():
    c = one(orientation="horizontal", markers=("few_lines",))
    assert c["counts"]["sd"] == 0 and c["patches"][8]["kind"] == "paper"


def test_the_xml_writes_sd_patches_as_slur_patches_with_the_template_ids():
    import xml.etree.ElementTree as ET
    c = one(orientation="horizontal", markers=("sd_pair",))
    root = ET.fromstring(c["xml"])
    seq = [t.get("rRef") for t in root.find("TemplatePatches")]
    assert seq[8:10] == ["P_0\u00b0K", "P_90\u00b0K"]
    d = {t.get("ID"): (t.get("Type"), t.find("PatchTypeData").attrib) for t in root.find("ReferencePatches")}
    assert d["P_0\u00b0K"] == ("Slur", {"Slot": "1", "Type": "Vertical"})
    assert d["P_90\u00b0K"] == ("Slur", {"Slot": "1", "Type": "Horizontal"})
    assert '<TemplatePatch Type="Slur" ID="P_0\u00b0K">\n\t\t\t<PatchTypeData Slot="1" Type="Vertical" />' in c["xml"]
    assert c["xml_notes"] == []


def test_sd_patches_in_a_spot_ink_use_the_spot_ink_slot():
    import xml.etree.ElementTree as ET
    c = one(orientation="horizontal", markers=("sd_pair_spot",))
    assert c["patches"][8]["label"] == "S/D vertical lines S1 100"
    root = ET.fromstring(c["xml"])
    d = {t.get("ID"): t.find("PatchTypeData").attrib for t in root.find("ReferencePatches")}
    assert d["P_0\u00b01"] == {"Slot": "5", "Type": "Vertical"} and d["P_90\u00b01"] == {"Slot": "5", "Type": "Horizontal"}


def test_sd_patches_keep_their_physical_direction_when_the_bar_is_read_from_the_other_end():
    c = one(orientation="horizontal", markers=("sd_pair",), corner_mark="end")
    assert c["direction"] == "right to left"
    assert [(p["slot"], p["lines"]["direction"]) for p in c["patches"] if p["kind"] == "lines"] == [
        (9, "horizontal"), (10, "vertical")]                       # the order flips, the lines do not


def test_sd_patches_are_found_on_a_vertical_bar_too():
    c = one(orientation="vertical", markers=("sd_pair",))
    assert [(p["slot"], p["lines"]["direction"]) for p in c["patches"] if p["kind"] == "lines"] == [
        (9, "vertical"), (10, "horizontal")]


def test_a_repeated_bar_with_sd_patches_folds_to_the_same_core_and_xml():
    single = one(orientation="horizontal", markers=("sd_pair",))
    for kw in ({"repeat": (3, 72.0)}, {"repeat": (3, 75.5)}, {"repeat": (3, 75.5), "as_form": True}):
        c = one(orientation="horizontal", markers=("sd_pair",), **kw)
        assert c["repeat"]["copies_along"] == 3 and c["counts"]["sd"] == 2 and c["xml"] == single["xml"], kw


def test_thin_tick_lines_are_not_sd_patches():
    # the hairline tick between slots 7 and 8 and the ordinary decoy shapes must not become patches
    c = one(orientation="horizontal", markers=("hairline",))
    assert c["counts"]["sd"] == 0


def test_patch_dimensions_in_the_xml_are_rounded_to_the_nearest_half_micrometre():
    assert core._dim(4.0623) == "4.0625" and core._dim(4.0627) == "4.0625" and core._dim(4.06) == "4.06"
    assert core._dim(3.0) == "3" and core._dim(2.9999) == "3" and core._dim(5.0001) == "5"
    assert core._dim(9.525) == "9.525" and core._dim(6.0001) == "6"
