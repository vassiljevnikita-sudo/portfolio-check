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


# ------------------------------------------------------------------ images

from portfolio_check import images as image_mod  # noqa: E402

HOST = "https://demo.example"


def img(name, *, loaded=True, rendered=True, complete=True, alt=""):
    return {
        "src": f"{HOST}/shots/{name}",
        "alt": alt,
        "complete": complete,
        "natural_width": 600 if loaded else 0,
        "has_src": True,
        "rendered": rendered,
    }


def page_with(images, responses=None, wait=12.5):
    return RenderResult(
        "https://demo.example/",
        "https://demo.example/",
        GOOD_TEXT,
        "",
        ["https://github.com/someone/"],
        "screenshots/demo-site.png",
        images=images,
        image_responses=responses or {},
        image_wait=wait,
    )


def test_render_passes_when_images_load_after_retries(ctx):
    site = ctx.config.sites[0]
    site.required_images = ["/bonbon/start.webp"]
    retried = f"{HOST}/shots/bonbon/start.webp"
    page = page_with(
        [img("bonbon/start.webp"), img("hidden.webp", loaded=False, rendered=False)],
        {retried: [429, 429, 200]},
    )
    ctx.render = lambda s: page
    result = render.run(ctx, site)
    assert result.status == PASS, result.evidence
    joined = "\n".join(result.evidence)
    assert "required image loaded: '/bonbon/start.webp'" in joined
    assert f"image loaded after failed requests: {retried} (requests: 429, 429, 200)" in joined
    assert result.data["broken_images"] == []
    assert result.data["images_shown"] == 1


def test_render_fails_on_a_broken_image_and_reports_its_status(ctx):
    site = ctx.config.sites[0]
    site.required_images = ["/arena/rangliste.webp", "/classic/start.webp"]
    broken = f"{HOST}/shots/arena/rangliste.webp?retry=3"
    page = page_with(
        [img("arena/rangliste.webp?retry=3", loaded=False, alt="Rangliste"), img("ok.webp")],
        {
            f"{HOST}/shots/arena/rangliste.webp": [429, 429, 429],
            broken: [429],
        },
    )
    ctx.render = lambda s: page
    result = render.run(ctx, site)
    assert result.status == FAIL
    joined = "\n".join(result.evidence)
    assert f"broken image (complete, naturalWidth 0): {broken} (HTTP 429; requests for this file: 429, 429, 429, 429)" in joined
    assert "required image not loaded: '/arena/rangliste.webp'" in joined
    assert "required image missing: '/classic/start.webp'" in joined
    assert result.data["broken_images"] == [{"url": broken, "status": 429, "alt": "Rangliste"}]


def test_hidden_or_unstarted_images_do_not_fail(ctx):
    site = ctx.config.sites[0]
    page = page_with(
        [
            img("display-none.webp", loaded=False, rendered=False),
            img("lazy-offscreen.webp", loaded=False, complete=False),
            img("ok.webp"),
        ]
    )
    ctx.render = lambda s: page
    result = render.run(ctx, site)
    assert result.status == PASS, result.evidence
    assert any("still loading at the end of the wait" in line for line in result.evidence)


class FakePage:
    """Returns scripted image lists; records scrolls and waits."""

    def __init__(self, states, height=2000, view=900):
        self.states = list(states)
        self.height = height
        self.view = view
        self.scrolled_to = []
        self.focused = []
        self.waited = 0

    def evaluate(self, js, arg=None):
        if js == image_mod.IMAGES_JS:
            return self.states.pop(0) if len(self.states) > 1 else self.states[0]
        if js == image_mod.SCROLL_TO_JS:
            self.focused.append(arg)
            return None
        if "scrollHeight" in js:
            return self.height
        if "innerHeight" in js:
            return self.view
        if "scrollTo" in js:
            self.scrolled_to.append(arg)
        return None

    def wait_for_timeout(self, ms):
        self.waited += ms


class FakeClock:
    def __init__(self, page):
        self.page = page

    def __call__(self):
        return self.page.waited / 1000


def test_scroll_through_reaches_the_bottom():
    page = FakePage([[]], height=2000, view=900)
    image_mod.scroll_through(page, pause_ms=10)
    assert page.scrolled_to == [0, 720, 1440]


def test_wait_for_images_stops_once_everything_is_loaded():
    first = [img("a.webp", loaded=False), img("b.webp")]
    page = FakePage([first, first, [img("a.webp"), img("b.webp")]])
    images, waited = image_mod.wait_for_images(page, timeout=45, clock=FakeClock(page))
    assert all(image_mod.is_loaded(i) for i in images)
    assert waited == 2  # two polls of one second each
    assert page.focused == [f"{HOST}/shots/a.webp"] * 2


def test_wait_for_images_is_bounded():
    page = FakePage([[img("a.webp", loaded=False)]])
    images, waited = image_mod.wait_for_images(page, timeout=5, clock=FakeClock(page))
    assert waited == 5
    assert image_mod.is_broken(images[0])


def test_wait_for_images_waits_for_required_hidden_images():
    hidden = img("rangliste.webp", rendered=False, complete=False)
    page = FakePage([[hidden], [img("rangliste.webp", rendered=False)]])
    images, waited = image_mod.wait_for_images(page, required=["rangliste"], timeout=45, clock=FakeClock(page))
    assert waited == 1 and image_mod.is_loaded(images[0])


def test_statuses_group_requests_for_the_same_file():
    responses = {f"{HOST}/a.webp?v=1": [429, 429], f"{HOST}/a.webp?v=1&retry=3": [200], f"{HOST}/b.webp": [200]}
    assert image_mod.statuses(f"{HOST}/a.webp?v=1&retry=3", responses) == (200, [429, 429, 200])
    assert image_mod.statuses(f"{HOST}/c.webp", responses) == (None, [])
