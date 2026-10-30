"""The render check in a real Chromium against a local server that throttles one image.

Skipped when Playwright's Chromium is not installed (python -m playwright install chromium).
"""

from __future__ import annotations

import base64
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

import pytest

from portfolio_check.checks import render
from portfolio_check.config import SiteTarget
from portfolio_check.result import FAIL, PASS

# A 1x1 PNG.
PNG = base64.b64decode(
    "iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAYAAAAfFcSJAAAADUlEQVR42mP8z8BQDwAEhQGAhKmMIQAAAABJRU5ErkJggg=="
)
FOOTER = '<p>Built by Test Person</p><a href="https://github.com/someone">GitHub</a>'
PAGES = {
    # Loads after scrolling (lazy, far below); a broken image inside display:none is ignored.
    "/ok.html": f"""<!doctype html><html><body>
        <img src="/ok.png" width="100" height="100" alt="ok">
        <div style="height:3000px"></div>
        <img src="/lazy.png" loading="lazy" width="100" height="100" alt="lazy">
        <div style="display:none"><img src="/throttled.png" alt="hidden"></div>
        {FOOTER}</body></html>""",
    # The host answers 429 for this image on every request.
    "/throttled.html": f"""<!doctype html><html><body>
        <img src="/ok.png" width="100" height="100" alt="ok">
        <img src="/throttled.png?v=1" width="100" height="100" alt="throttled">
        {FOOTER}</body></html>""",
}


class Handler(BaseHTTPRequestHandler):
    def do_GET(self):  # noqa: N802 - http.server API
        path = self.path.split("?")[0]
        if path in PAGES:
            body, status, kind = PAGES[path].encode(), 200, "text/html; charset=utf-8"
        elif path in ("/ok.png", "/lazy.png"):
            body, status, kind = PNG, 200, "image/png"
        elif path == "/throttled.png":
            body, status, kind = b"Too Many Requests", 429, "text/plain"
        else:
            body, status, kind = b"not found", 404, "text/plain"
        self.send_response(status)
        self.send_header("Content-Type", kind)
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def log_message(self, *args):
        pass


@pytest.fixture(scope="module")
def server():
    httpd = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
    thread = threading.Thread(target=httpd.serve_forever, daemon=True)
    thread.start()
    yield f"http://127.0.0.1:{httpd.server_port}"
    httpd.shutdown()


@pytest.fixture
def browser_ctx(ctx):
    sync_api = pytest.importorskip("playwright.sync_api")
    try:
        with sync_api.sync_playwright() as p:
            p.chromium.launch().close()
    except Exception as exc:  # noqa: BLE001
        pytest.skip(f"Chromium not available: {exc}")
    yield ctx
    ctx.close()


def site(url, **extra):
    return SiteTarget(
        name="fixture",
        url=url,
        required_phrases=["Built by Test Person"],
        required_links=["https://github.com/someone"],
        image_wait=3,
        **extra,
    )


def test_page_with_loaded_images_passes(browser_ctx, server):
    result = render.run(browser_ctx, site(f"{server}/ok.html", required_images=["/lazy.png"]))
    assert result.status == PASS, result.evidence
    assert result.data["images_shown"] == 2 and result.data["images_loaded"] == 2


def test_throttled_image_fails_with_its_status(browser_ctx, server):
    result = render.run(browser_ctx, site(f"{server}/throttled.html"))
    assert result.status == FAIL
    url = f"{server}/throttled.png?v=1"
    assert result.data["broken_images"] == [{"url": url, "status": 429, "alt": "throttled"}]
    assert any(line.startswith(f"broken image (complete, naturalWidth 0): {url} (HTTP 429") for line in result.evidence)
