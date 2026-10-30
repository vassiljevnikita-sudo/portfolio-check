import pytest

from portfolio_check.checks import cv_form
from portfolio_check.result import FAIL, PASS

from .conftest import FakeResponse
from .cv_factory import make_cv


@pytest.fixture
def cv(ctx, tmp_path, monkeypatch):
    path = make_cv(tmp_path / "cv.docx")
    ctx.local_paths["cv-en"] = path
    return ctx.config.cvs[0]


def _pages(n):
    def fake(ctx, docx):
        pdf = ctx.out_dir / "cv-pdf" / "cv.pdf"
        pdf.parent.mkdir(parents=True, exist_ok=True)
        pdf.write_bytes(b"%PDF")
        return n, pdf

    return fake


def _lt(matches):
    def post(url, data=None, timeout=None):
        assert data["language"] == "en-US"
        return FakeResponse(json_data={"matches": matches(data["text"])})

    return post


def test_cv_form_passes_with_allowlisted_terms(ctx, cv, monkeypatch):
    monkeypatch.setattr(cv_form, "pdf_pages", _pages(2))

    def matches(text):
        i = text.index("PowerShell")  # allowlisted technical term
        return [{"offset": i, "length": 10, "rule": {"id": "MORFOLOGIK_RULE_EN_US"}, "message": "spelling"}]

    ctx.http.post = _lt(matches)
    result = cv_form.run(ctx, cv)
    assert result.status == PASS, result.evidence


def test_cv_form_fails_on_three_pages_and_spelling(ctx, cv, monkeypatch):
    monkeypatch.setattr(cv_form, "pdf_pages", _pages(3))

    def matches(text):
        i = text.index("Pharmacy")
        return [{"offset": i, "length": 8, "rule": {"id": "TYPO"}, "message": "typo", "context": {"text": "Pharmacy student"}}]

    ctx.http.post = _lt(matches)
    result = cv_form.run(ctx, cv)
    assert result.status == FAIL
    joined = "\n".join(result.evidence)
    assert "3 page(s)" in joined and "TYPO" in joined


def test_cv_form_fails_on_nested_tables(ctx, tmp_path, monkeypatch):
    ctx.local_paths["cv-en"] = make_cv(tmp_path / "nested.docx", nested_table=True)
    monkeypatch.setattr(cv_form, "pdf_pages", _pages(1))
    ctx.http.post = _lt(lambda text: [])
    result = cv_form.run(ctx, ctx.config.cvs[0])
    assert result.status == FAIL and any("nested table" in e for e in result.evidence)


def test_cv_form_fails_when_languagetool_is_down(ctx, cv, monkeypatch):
    monkeypatch.setattr(cv_form, "pdf_pages", _pages(1))
    ctx.http.post = lambda *a, **k: FakeResponse(status=503, text="busy")
    result = cv_form.run(ctx, cv)
    assert result.status == FAIL and any("spelling check unavailable" in e for e in result.evidence)


def test_spellcheck_text_splits_paragraphs_and_tab_columns():
    text = "PROJECTS\nReleased\tQuestify web app\n•\tBullet text"
    assert cv_form.spellcheck_text(text) == "PROJECTS\n\nReleased\n\nQuestify web app\n\nBullet text"


def test_span_allowlist_exempts_a_quoted_passage_only(ctx, cv, monkeypatch):
    monkeypatch.setattr(cv_form, "pdf_pages", _pages(1))
    ctx.config.allowlist.append("span: German, English.")

    def matches(text):
        inside = text.index("German")
        outside = text.index("Pharmacy")
        return [
            {"offset": inside, "length": 6, "rule": {"id": "TYPO"}, "message": "typo"},
            {"offset": outside, "length": 8, "rule": {"id": "TYPO"}, "message": "typo"},
        ]

    ctx.http.post = _lt(matches)
    result = cv_form.run(ctx, cv)
    issues = [e for e in result.evidence if e.startswith("  TYPO")]
    assert result.status == FAIL and len(issues) == 1 and "Pharmacy" in issues[0]
