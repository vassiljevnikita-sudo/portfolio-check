import requests

from portfolio_check.checks import links
from portfolio_check.context import RenderResult
from portfolio_check.result import FAIL, PASS

from .conftest import FakeResponse


def _fake_fetch(table):
    def fetch(url, timeout=None):
        entry = table[url]
        if isinstance(entry, Exception):
            raise entry
        status, final = entry if isinstance(entry, tuple) else (entry, url)
        return FakeResponse(status=status, url=final)

    return fetch


def _repo(ctx, tmp_path, body):
    d = tmp_path / "repo"
    d.mkdir()
    (d / "README.md").write_text(body)
    (d / "docs").mkdir()
    (d / "docs" / "shot.png").write_bytes(b"png")
    ctx.local_paths["demo-repo"] = d
    return ctx.config.repos[0]


def test_links_pass_when_all_return_200(ctx, tmp_path):
    repo = _repo(ctx, tmp_path, "# X\n[a](https://a.example/) ![s](docs/shot.png) https://b.example/page\n")
    ctx.fetch = _fake_fetch({"https://a.example/": 200, "https://b.example/page": 200})
    result = links.run(ctx, repo)
    assert result.status == PASS, result.evidence
    assert result.data == {"checked": 2, "broken": 0}


def test_links_fail_on_404_login_redirect_timeout_and_missing_file(ctx, tmp_path):
    repo = _repo(
        ctx,
        tmp_path,
        "# X\n[a](https://a.example/) [p](https://private.example/x) [t](https://slow.example/) [m](docs/gone.md)\n",
    )
    ctx.fetch = _fake_fetch(
        {
            "https://a.example/": 404,
            "https://private.example/x": (200, "https://github.com/login?return_to=x"),
            "https://slow.example/": requests.Timeout(),
        }
    )
    result = links.run(ctx, repo)
    assert result.status == FAIL
    joined = "\n".join(result.evidence)
    assert "404 https://a.example/" in joined
    assert "redirects to login page" in joined
    assert "timeout" in joined
    assert "MISSING local file docs/gone.md" in joined


def test_rate_limit_is_retried():
    calls = []

    class Ctx:
        config = type("C", (), {"link_timeout": 10})()

        def fetch(self, url, timeout=None):
            calls.append(url)
            status = 429 if len(calls) == 1 else 200
            return FakeResponse(status=status, url=url, headers={"Retry-After": "1"})

    ok, line = links.check_url(Ctx(), "https://github.com/x", sleep=lambda s: None)
    assert ok and len(calls) == 2


def _always_429_ctx(calls, headers=None):
    class Ctx:
        config = type("C", (), {"link_timeout": 10})()

        def fetch(self, url, timeout=None):
            calls.append(url)
            return FakeResponse(status=429, url=url, headers=headers or {})

    return Ctx()


def test_expo_429_without_retry_after_backs_off_10_20_40_then_fails_as_throttled():
    calls, waits = [], []
    url = "https://mastertasker.expo.app/willkommen/"
    ok, line = links.check_url(_always_429_ctx(calls), url, sleep=waits.append)
    assert not ok
    assert waits == [10.0, 20.0, 40.0]
    assert len(calls) == 4
    assert line == f"429 {url} (throttled by host: still 429 after 3 retries)"


def test_expo_429_honours_retry_after_capped_at_30_s():
    calls, waits = [], []
    ctx = _always_429_ctx(calls, headers={"Retry-After": "120"})
    links.check_url(ctx, "https://simple-light-qbd.expo.app/", sleep=waits.append)
    assert waits == [30.0, 30.0, 30.0]


def test_other_hosts_keep_the_short_backoff():
    waits = []
    links.check_url(_always_429_ctx([]), "https://a.example/", sleep=waits.append)
    assert waits == [5.0, 10.0, 15.0]


def test_expo_hosts_get_one_request_at_a_time():
    assert links.host_concurrency("mastertasker.expo.app") == 1
    assert links.host_concurrency("simple-light-qbd.expo.app") == 1
    assert links.host_concurrency("github.com") == 2
    assert links.host_concurrency("expo.dev") == links.DEFAULT_CONCURRENCY
    assert links.host_concurrency("notexpo.app") == links.DEFAULT_CONCURRENCY


def test_site_links_come_from_the_rendered_page(ctx):
    site = ctx.config.sites[0]
    ctx.render = lambda s: RenderResult(s.url, s.url, "", "", ["https://demo.example/#top", "mailto:a@b.c", "https://x.example/404"])
    ctx.fetch = _fake_fetch({"https://demo.example/": 200, "https://x.example/404": 404})
    result = links.run(ctx, site)
    assert result.status == FAIL
    assert any(e.startswith("skip mailto:") for e in result.evidence)


def test_cv_links_include_relationships_and_typed_urls(ctx, tmp_path):
    from docx import Document
    from docx.opc.constants import RELATIONSHIP_TYPE as RT
    from docx.oxml import OxmlElement
    from docx.oxml.ns import qn

    doc = Document()
    p = doc.add_paragraph("Profile: ")
    rid = doc.part.relate_to("https://rel.example/profile", RT.HYPERLINK, is_external=True)
    h = OxmlElement("w:hyperlink")
    h.set(qn("r:id"), rid)
    r = OxmlElement("w:r")
    t = OxmlElement("w:t")
    t.text = "my profile"
    r.append(t)
    h.append(r)
    p._p.append(h)
    doc.add_paragraph("Code: github.com/someone and www.typed.example/page.")
    path = tmp_path / "cv.docx"
    doc.save(path)

    found = links.docx_links(path)
    assert "https://rel.example/profile" in found
    assert "https://github.com/someone" in found
    assert "https://www.typed.example/page" in found


def _scholar_case(ctx, tmp_path, monkeypatch, on_ci):
    repo = _repo(ctx, tmp_path, "# X\n[s](https://scholar.google.com/citations?user=x) [o](https://other.example/)\n")
    ctx.config.link_exceptions = [{"host": "scholar.google.com", "status": [403], "reason": "blocks runners"}]
    monkeypatch.setenv("GITHUB_ACTIONS", "true" if on_ci else "false")
    return repo


def test_link_exception_reports_unverified_on_ci_runner(ctx, tmp_path, monkeypatch):
    repo = _scholar_case(ctx, tmp_path, monkeypatch, on_ci=True)
    ctx.fetch = _fake_fetch({"https://scholar.google.com/citations?user=x": 403, "https://other.example/": 200})
    result = links.run(ctx, repo)
    assert result.status == PASS, result.evidence
    assert result.data == {"checked": 2, "broken": 0, "unverified": 1}
    assert "not verified from CI: blocks runners" in "\n".join(result.evidence)


def test_link_exception_does_not_apply_locally_or_to_other_statuses(ctx, tmp_path, monkeypatch):
    repo = _scholar_case(ctx, tmp_path, monkeypatch, on_ci=False)
    ctx.fetch = _fake_fetch({"https://scholar.google.com/citations?user=x": 403, "https://other.example/": 200})
    assert links.run(ctx, repo).status == FAIL

    monkeypatch.setenv("GITHUB_ACTIONS", "true")
    ctx.fetch = _fake_fetch({"https://scholar.google.com/citations?user=x": 404, "https://other.example/": 200})
    assert links.run(ctx, repo).status == FAIL

    ctx.fetch = _fake_fetch({"https://scholar.google.com/citations?user=x": 403, "https://other.example/": 403})
    result = links.run(ctx, repo)
    assert result.status == FAIL
    assert result.data["broken"] == 1 and result.data["unverified"] == 1


class _ApiResponse:
    def __init__(self, status, body=None):
        self.status_code = status
        self._body = body or {}

    def json(self):
        return self._body


def _rate_limited_ctx(api):
    class Ctx:
        config = type("C", (), {"link_timeout": 10})()

        def fetch(self, url, timeout=None):
            return FakeResponse(status=429, url=url, headers={"Retry-After": "1"})

        def github_api(self, path, params=None):
            return api(path, params)

    return Ctx()


def test_github_page_stuck_at_429_is_verified_via_api():
    def api(path, params):
        if path == "repos/o/r":
            return _ApiResponse(200, {"private": False})
        assert path == "repos/o/r/contents/docs/a.md" and params == {"ref": "main"}
        return _ApiResponse(200)

    ok, line = links.check_url(_rate_limited_ctx(api), "https://github.com/o/r/blob/main/docs/a.md",
                               sleep=lambda s: None)
    assert ok and "verified via GitHub API" in line


def test_api_fallback_fails_for_private_repo_or_missing_path():
    private = lambda path, params: _ApiResponse(200, {"private": True})
    ok, line = links.check_url(_rate_limited_ctx(private), "https://github.com/o/r", sleep=lambda s: None)
    assert not ok and "not public" in line

    def missing(path, params):
        return _ApiResponse(200, {"private": False}) if path == "repos/o/r" else _ApiResponse(404)

    ok, line = links.check_url(_rate_limited_ctx(missing), "https://github.com/o/r/blob/main/gone.md",
                               sleep=lambda s: None)
    assert not ok and "404 for path" in line


def test_api_fallback_does_not_cover_other_github_pages():
    api = lambda path, params: _ApiResponse(200, {"private": False})
    ok, line = links.check_url(_rate_limited_ctx(api), "https://github.com/o/r/releases/latest/download/x.apk",
                               sleep=lambda s: None)
    assert not ok and "no API fallback" in line
