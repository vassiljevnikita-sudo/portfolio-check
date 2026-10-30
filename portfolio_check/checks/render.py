"""Demo render: open each site in a fresh headless Chromium and read what a visitor sees.

Pass when every required phrase and link is present, no forbidden phrase or pattern
appears, and at least `min_numbers` numeric values are visible.
"""

from __future__ import annotations

import re

from ..result import failed, passed

NAME = "render"

NUMBER_RE = re.compile(r"[-+−]?\d+(?:[.,  ]\d+)*\s?%?")
DATE_RE = re.compile(r"^\d{1,2}[./]\d{1,2}[./]\d{2,4}$")
YEAR_RE = re.compile(r"^(19|20)\d\d$")


def visible_numbers(text: str) -> list[str]:
    """Numeric values in the visible text. Dates and bare years are not counted."""
    out = []
    for m in NUMBER_RE.finditer(text):
        token = m.group(0).strip()
        bare = token.lstrip("+-−").rstrip("% ")
        if DATE_RE.match(bare) or YEAR_RE.match(bare):
            continue
        out.append(token)
    return out


def _norm_link(url: str) -> str:
    return url.split("#")[0].rstrip("/").lower()


def evaluate(site, page) -> tuple[bool, str, list[str], dict]:
    problems: list[str] = []
    info: list[str] = []
    text_l = page.text.lower()

    for phrase in site.required_phrases:
        if phrase.lower() in text_l:
            info.append(f"found required phrase: {phrase!r}")
        else:
            problems.append(f"missing required phrase: {phrase!r}")
    for phrase in site.forbidden_phrases:
        if phrase.lower() in text_l:
            problems.append(f"forbidden phrase present: {phrase!r}")
    for pattern in site.forbidden_patterns:
        hits = re.findall(pattern, page.text, re.M)
        if hits:
            problems.append(f"forbidden pattern {pattern!r} matched {len(hits)} time(s)")

    have = {_norm_link(link) for link in page.links}
    for link in site.required_links:
        if _norm_link(link) in have:
            info.append(f"found required link: {link}")
        else:
            problems.append(f"missing required link: {link}")

    numbers = visible_numbers(page.text)
    if len(numbers) < site.min_numbers:
        problems.append(f"only {len(numbers)} numeric values visible, need {site.min_numbers}")
    else:
        info.append(f"{len(numbers)} numeric values visible (need {site.min_numbers})")
    if numbers:
        info.append("numbers seen: " + ", ".join(numbers[:12]) + (" ..." if len(numbers) > 12 else ""))

    data = {"numbers": len(numbers), "final_url": page.final_url, "screenshot": page.screenshot}
    summary = f"{len(problems)} problem(s)" + (f", first: {problems[0]}" if problems else "")
    return not problems, summary, problems + info, data


def run(ctx, site):
    page = ctx.render(site)
    if page.error:
        return failed(NAME, site.name, f"page did not render: {page.error}", [f"url: {site.url}"])
    ok, summary, evidence, data = evaluate(site, page)
    evidence = [f"screenshot: {page.screenshot}", f"final url: {page.final_url}", *evidence]
    if ok:
        return passed(NAME, site.name, "all phrase, link and number rules met", evidence, **data)
    return failed(NAME, site.name, summary, evidence, **data)
