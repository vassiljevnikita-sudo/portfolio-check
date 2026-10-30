import subprocess

from portfolio_check.checks import denylist
from portfolio_check.context import RenderResult
from portfolio_check.result import FAIL, PASS

# Synthetic terms only. Real terms live in the DENYLIST secret, never in this repo.
TERMS = ["Acme Pharma", "ZX-SECRET-77"]


def _git(d, *args):
    subprocess.run(["git", "-C", str(d), *args], check=True, capture_output=True,
                   env={"GIT_AUTHOR_NAME": "t", "GIT_AUTHOR_EMAIL": "t@example.com",
                        "GIT_COMMITTER_NAME": "t", "GIT_COMMITTER_EMAIL": "t@example.com", "PATH": "/usr/bin:/bin"})


def _repo(ctx, tmp_path, files):
    d = tmp_path / "repo"
    d.mkdir()
    _git(d, "init", "-q")
    for name, content in files.items():
        (d / name).write_text(content)
    _git(d, "add", "-A")
    _git(d, "commit", "-q", "-m", "init")
    ctx.local_paths["demo-repo"] = d
    return d


def test_denylist_passes_on_clean_repo(ctx, tmp_path):
    ctx.denylist = TERMS
    _repo(ctx, tmp_path, {"README.md": "Synthetic sample data only.\n"})
    assert denylist.run(ctx, ctx.config.repos[0]).status == PASS


def test_denylist_finds_terms_in_files_and_history_without_printing_them(ctx, tmp_path):
    ctx.denylist = TERMS
    d = _repo(ctx, tmp_path, {"README.md": "Made at ACME PHARMA.\n", "old.txt": "batch zx-secret-77\n"})
    (d / "old.txt").unlink()
    _git(d, "commit", "-qam", "remove old file")
    result = denylist.run(ctx, ctx.config.repos[0])
    assert result.status == FAIL
    joined = "\n".join(result.evidence)
    assert "term #1 in README.md:1" in joined
    assert "term #2 in history commit" in joined and "(old.txt)" in joined
    for term in TERMS:
        assert term.lower() not in joined.lower()
        assert term.lower() not in result.summary.lower()


def test_denylist_marks_hits_inside_longer_words():
    hits = denylist.find_hits("acmeworks\nacme", ["acme"])
    assert hits == [(1, 1, False), (1, 2, True)]


def test_denylist_without_terms_fails(ctx):
    ctx.denylist = []
    assert denylist.run(ctx, ctx.config.repos[0]).status == FAIL


def test_denylist_scans_rendered_site_and_same_origin_pages(ctx):
    ctx.denylist = TERMS
    site = ctx.config.sites[0]
    ctx.render = lambda s: RenderResult(s.url, s.url, "clean text", "<p>clean</p>", ["https://demo.example/imprint.html", "https://other.example/"])
    fetched = []

    def get_text(url):
        fetched.append(url)
        return 200, "Responsible: Acme Pharma GmbH"

    ctx.get_text = get_text
    result = denylist.run(ctx, site)
    assert result.status == FAIL
    assert fetched == ["https://demo.example/imprint.html"]


def test_load_terms_ignores_blank_lines_and_comments():
    assert denylist.load_terms("a\n\n# note\n b \n") == ["a", "b"]
