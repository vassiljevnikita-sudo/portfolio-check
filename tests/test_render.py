from portfolio_check.checks import render
from portfolio_check.context import RenderResult
from portfolio_check.result import FAIL, PASS

GOOD_TEXT = """Built by Test Person
Portfolio value 9.671,90 EUR
Return -3,26 %
Trades 120
Sharpe 1.4
As of 29.10.2026
"""

BAD_TEXT = """Portfolio value
—
Keine Messwerte
Updated 2026
"""


def test_render_passes(ctx):
    site = ctx.config.sites[0]
    ctx.render = lambda s: RenderResult(s.url, s.url, GOOD_TEXT, "", ["https://github.com/someone/"], "screenshots/demo-site.png")
    result = render.run(ctx, site)
    assert result.status == PASS, result.evidence
    assert result.data["numbers"] == 4  # the date does not count


def test_render_fails_on_placeholders_missing_phrases_and_numbers(ctx):
    site = ctx.config.sites[0]
    ctx.render = lambda s: RenderResult(s.url, s.url, BAD_TEXT, "", [], "screenshots/demo-site.png")
    result = render.run(ctx, site)
    assert result.status == FAIL
    joined = "\n".join(result.evidence)
    assert "missing required phrase: 'Built by Test Person'" in joined
    assert "forbidden phrase present: 'Keine Messwerte'" in joined
    assert "forbidden pattern" in joined
    assert "missing required link" in joined
    assert "only 0 numeric values" in joined


def test_render_error_is_a_failure(ctx):
    site = ctx.config.sites[0]
    ctx.render = lambda s: RenderResult(s.url, s.url, "", "", [], None, "TimeoutError: networkidle")
    assert render.run(ctx, site).status == FAIL


def test_visible_numbers_skip_dates_and_years():
    assert render.visible_numbers("29.10.2026 2026 1,63 +220,21 % 42") == ["1,63", "+220,21 %", "42"]
