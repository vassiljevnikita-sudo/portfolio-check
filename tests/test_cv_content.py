from portfolio_check.checks import cv_content
from portfolio_check.result import FAIL, PASS, SKIP

from .cv_factory import GOOD_EN, make_cv


def test_cv_content_passes(ctx, tmp_path):
    ctx.local_paths["cv-en"] = make_cv(tmp_path / "cv.docx")
    result = cv_content.run(ctx, ctx.config.cvs[0])
    assert result.status == PASS, result.evidence


def test_cv_content_fails_on_forbidden_wording_and_missing_claims(ctx, tmp_path):
    items = [(k, t.replace("designed along GAMP 5 principles", "GAMP 5 compliant")) for k, t in GOOD_EN]
    items = [(k, t) for k, t in items if t != "Languages"]
    ctx.local_paths["cv-en"] = make_cv(tmp_path / "cv.docx", items)
    result = cv_content.run(ctx, ctx.config.cvs[0])
    assert result.status == FAIL
    joined = "\n".join(result.evidence)
    assert "forbidden phrase present: 'GAMP 5 compliant'" in joined
    assert "claim 'gamp-wording' not matched" in joined
    assert "missing section: Languages" in joined


def test_cv_skipped_when_not_provided(ctx):
    assert cv_content.run(ctx, ctx.config.cvs[0]).status == SKIP


def test_cv_fails_when_not_provided_and_required(ctx):
    ctx.require_cv = True
    assert cv_content.run(ctx, ctx.config.cvs[0]).status == FAIL


def test_cv_fails_when_explicit_path_missing(ctx, tmp_path, monkeypatch):
    monkeypatch.setenv("TEST_CV_EN", str(tmp_path / "nope.docx"))
    result = cv_content.run(ctx, ctx.config.cvs[0])
    assert result.status == FAIL and result.summary.startswith("file missing")
