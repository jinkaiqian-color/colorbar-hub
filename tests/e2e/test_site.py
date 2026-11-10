"""End-to-end check of the published page, including the in-browser Python engine.

Needs: pip install playwright pytest && playwright install chromium
Needs a built site: python tools/build_site.py
"""
import functools
import http.server
import os
import threading

import pytest

playwright = pytest.importorskip("playwright.sync_api")

ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
SITE = os.path.join(ROOT, "_site")


@pytest.fixture(scope="module")
def base_url():
    if not os.path.isdir(SITE):
        pytest.skip("run `python tools/build_site.py` first")
    handler = functools.partial(http.server.SimpleHTTPRequestHandler, directory=SITE)
    handler.log_message = lambda *a, **k: None
    srv = http.server.ThreadingHTTPServer(("127.0.0.1", 0), handler)
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    yield "http://127.0.0.1:%d/" % srv.server_address[1]
    srv.shutdown()


def test_sample_sheet_is_analyzed_in_the_browser(base_url):
    with playwright.sync_playwright() as p:
        browser = p.chromium.launch()
        page = browser.new_page()
        errors = []
        page.on("pageerror", lambda e: errors.append(str(e)))
        page.goto(base_url)
        page.wait_for_selector("#engine.ready", timeout=240_000)   # Pyodide + pypdf download
        page.click("#sample")
        page.wait_for_selector("#out.on", timeout=120_000)
        assert "4.000 × 6.000" in page.inner_text("#summary")
        summary = page.inner_text("#summary")
        assert "Right to left" in summary                          # read from the corner mark
        assert "Found, sets slot 1" in summary
        rows = page.locator("#tbl tbody tr")
        assert rows.count() == 18                                  # every slot has a row
        assert [rows.nth(i).locator("td").first.inner_text() for i in range(18)] == [str(i) for i in range(1, 19)]
        assert "NU" in page.inner_text("#tbl")
        assert page.locator("#dlCsv, #dlJson").count() == 0        # only XML is offered
        with page.expect_download() as download:
            page.click("#dlXml")
        xml = open(download.value.path(), encoding="utf-8").read()
        assert xml.startswith('<?xml version="1.0"?>') and '<ColorBar Name="sample_bar">' in xml
        assert xml.count("<TemplatePatch rRef=") == 18 and 'rRef="P_NU"' in xml
        assert download.value.suggested_filename == "colorbar_sample_bar.xml"
        assert errors == []
        browser.close()


def test_a_step_and_repeat_sheet_shows_only_the_core(base_url, tmp_path):
    import sys
    sys.path.insert(0, os.path.join(ROOT, "tests"))
    from make_sample import build_pdf
    pdf = tmp_path / "repeated.pdf"
    pdf.write_bytes(build_pdf("horizontal", repeat=(3, 75.5)))
    with playwright.sync_playwright() as p:
        browser = p.chromium.launch()
        page = browser.new_page()
        page.goto(base_url)
        page.wait_for_selector("#engine.ready", timeout=240_000)
        page.set_input_files("#file", str(pdf))
        page.wait_for_selector("#out.on", timeout=120_000)
        assert "Step and repeat found" in page.inner_text("#summary")
        assert "3 times along the bar" in page.inner_text("#summary")
        assert page.locator("#tbl tbody tr").count() == 18         # one core, not three
        browser.close()


def test_a_label_that_contradicts_its_patch_can_be_followed_or_not(base_url, tmp_path):
    import sys
    sys.path.insert(0, os.path.join(ROOT, "tests"))
    from make_sample import build_pdf
    pdf = tmp_path / "labelled.pdf"
    pdf.write_bytes(build_pdf("horizontal", markers=("label_wrong",)))
    with playwright.sync_playwright() as p:
        browser = p.chromium.launch()
        page = browser.new_page()
        page.goto(base_url)
        page.wait_for_selector("#engine.ready", timeout=240_000)
        page.set_input_files("#file", str(pdf))
        page.wait_for_selector("#out.on", timeout=120_000)
        assert "The printed label says 75% but the patch is C 100% at slot 2." in page.inner_text("#summary")
        assert page.is_checked("#useLabels")
        with page.expect_download() as d1:
            page.click("#dlXml")
        assert 'rRef="P_TC"' in open(d1.value.path(), encoding="utf-8").read()
        page.uncheck("#useLabels")
        with page.expect_download() as d2:
            page.click("#dlXml")
        assert 'rRef="P_TC"' not in open(d2.value.path(), encoding="utf-8").read()
        browser.close()


def test_sd_patches_are_listed_and_written_as_slur_patches(base_url, tmp_path):
    import sys
    sys.path.insert(0, os.path.join(ROOT, "tests"))
    from make_sample import build_pdf
    pdf = tmp_path / "sd.pdf"
    pdf.write_bytes(build_pdf("horizontal", markers=("sd_pair",)))
    with playwright.sync_playwright() as p:
        browser = p.chromium.launch()
        page = browser.new_page()
        page.goto(base_url)
        page.wait_for_selector("#engine.ready", timeout=240_000)
        page.set_input_files("#file", str(pdf))
        page.wait_for_selector("#out.on", timeout=120_000)
        assert "S/D patches" in page.inner_text("#summary")
        assert "S/D vertical lines K100" in page.inner_text("#tbl")
        with page.expect_download() as d:
            page.click("#dlXml")
        xml = open(d.value.path(), encoding="utf-8").read()
        assert 'Type="Slur"' in xml and "P_0\u00b0K" in xml and "P_90\u00b0K" in xml
        browser.close()
