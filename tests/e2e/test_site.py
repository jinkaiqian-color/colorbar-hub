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
