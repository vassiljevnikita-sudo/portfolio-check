"""Demo render: open each site in a fresh headless Chromium and read what a visitor sees.

Pass when every required phrase and link is present, no forbidden phrase or pattern
appears, at least `min_numbers` numeric values are visible, no shown image is broken
after the image wait, and every `required_images` entry matches a loaded image.
"""

from __future__ import annotations

import re

from ..images import is_broken, is_loaded, is_shown, matching, statuses
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

    image_problems, image_info, image_data = check_images(site, page)
    problems += image_problems
    info += image_info

    data = {"numbers": len(numbers), "final_url": page.final_url, "screenshot": page.screenshot, **image_data}
    summary = f"{len(problems)} problem(s)" + (f", first: {problems[0]}" if problems else "")
    return not problems, summary, problems + info, data


def _status_text(url: str, responses: dict) -> tuple[object, str]:
    last, history = statuses(url, responses)
    if last is None and not history:
        return None, "no HTTP response recorded"
    text = f"HTTP {last}" if last is not None else "no response for this exact URL"
    if len(history) > 1:
        text += f"; requests for this file: {', '.join(str(s) for s in history)}"
    return last, text


def check_images(site, page) -> tuple[list[str], list[str], dict]:
    """Broken shown images and missing required images, with HTTP status as evidence."""
    problems: list[str] = []
    info: list[str] = []
    images = page.images
    shown = [img for img in images if is_shown(img)]
    broken = []
    for img in shown:
        if not is_broken(img):
            continue
        status, text = _status_text(img["src"], page.image_responses)
        broken.append({"url": img["src"], "status": status, "alt": img.get("alt", "")})
        problems.append(f"broken image (complete, naturalWidth 0): {img['src']} ({text})")
    for pattern in site.required_images:
        found = matching(images, pattern)
        if not found:
            problems.append(f"required image missing: {pattern!r}")
        elif any(is_loaded(img) for img in found):
            info.append(f"required image loaded: {pattern!r}")
        else:
            problems.append(f"required image not loaded: {pattern!r} ({found[0]['src']})")
    loaded = [img for img in shown if is_loaded(img)]
    pending = [img for img in shown if not img["complete"]]
    if images:
        info.append(
            f"{len(loaded)} of {len(shown)} shown images loaded after a wait of {page.image_wait} s"
        )
    for img in pending:
        info.append(f"image still loading at the end of the wait: {img['src']}")
    # Images that needed more than one request but ended up loaded.
    for img in loaded:
        _, history = statuses(img["src"], page.image_responses)
        if any(not (isinstance(s, int) and s < 400) for s in history):
            info.append(
                f"image loaded after failed requests: {img['src']} "
                f"(requests: {', '.join(str(s) for s in history)})"
            )
    data = {
        "images_shown": len(shown),
        "images_loaded": len(loaded),
        "broken_images": broken,
    }
    return problems, info, data


def run(ctx, site):
    page = ctx.render(site)
    if page.error:
        return failed(NAME, site.name, f"page did not render: {page.error}", [f"url: {site.url}"])
    ok, summary, evidence, data = evaluate(site, page)
    evidence = [f"screenshot: {page.screenshot}", f"final url: {page.final_url}", *evidence]
    if ok:
        return passed(NAME, site.name, "all phrase, link, number and image rules met", evidence, **data)
    return failed(NAME, site.name, summary, evidence, **data)
