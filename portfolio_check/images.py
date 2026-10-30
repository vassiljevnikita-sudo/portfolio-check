"""Images on a rendered page: scroll through it, wait a bounded time, report broken ones.

An image counts as shown when it has a src and a layout box (it is not inside a
display: none subtree). It is broken when the browser finished it without pixels:
complete and naturalWidth == 0, for example after an HTTP 429.
"""

from __future__ import annotations

import time
from urllib.parse import urlsplit

IMAGES_JS = """() => [...document.images].map((img) => {
  const box = img.getBoundingClientRect();
  return {
    src: img.currentSrc || img.src,
    alt: img.alt,
    complete: img.complete,
    natural_width: img.naturalWidth,
    has_src: Boolean(img.getAttribute("src") || img.getAttribute("srcset")),
    rendered: img.getClientRects().length > 0 && box.width > 0 && box.height > 0,
  };
})"""

SCROLL_TO_JS = """(src) => {
  const img = [...document.images].find((i) => (i.currentSrc || i.src) === src);
  if (img) img.scrollIntoView({ block: "center" });
}"""


def is_loaded(img: dict) -> bool:
    return bool(img["complete"]) and img["natural_width"] > 0


def is_shown(img: dict) -> bool:
    return bool(img["has_src"]) and bool(img["rendered"])


def is_broken(img: dict) -> bool:
    return bool(img["has_src"]) and bool(img["complete"]) and img["natural_width"] == 0


def matching(images: list[dict], pattern: str) -> list[dict]:
    return [img for img in images if pattern in img["src"]]


def unresolved(images: list[dict], required=()) -> list[dict]:
    """Shown images that are not loaded, plus required images without a loaded match."""
    out = [img for img in images if is_shown(img) and not is_loaded(img)]
    for pattern in required:
        found = matching(images, pattern)
        if not any(is_loaded(img) for img in found):
            out.extend(img for img in found if img not in out)
    return out


def scroll_through(page, pause_ms: int = 250, max_steps: int = 80) -> None:
    """Scroll from top to bottom in steps of 80 % of the viewport, so lazy images start."""
    y = 0
    for _ in range(max_steps):
        height = page.evaluate("() => document.documentElement.scrollHeight")
        if y > height:
            break
        page.evaluate("(y) => window.scrollTo(0, y)", y)
        page.wait_for_timeout(pause_ms)
        y += max(200, int(page.evaluate("() => window.innerHeight") * 0.8))


def wait_for_images(page, required=(), timeout: float = 45.0, clock=time.monotonic):
    """Poll until every shown and required image is loaded or `timeout` seconds pass.

    While waiting, the page scrolls to one unresolved image per second, as a visitor
    looking at it would: lazy images start and in-page retry scripts get their turn.
    Returns (images, seconds waited).
    """
    start = clock()
    turn = 0
    while True:
        images = page.evaluate(IMAGES_JS)
        waiting = unresolved(images, required)
        waited = clock() - start
        if not waiting or waited >= timeout:
            return images, waited
        page.evaluate(SCROLL_TO_JS, waiting[turn % len(waiting)]["src"])
        turn += 1
        page.wait_for_timeout(1000)


def _same_file(a: str, b: str) -> bool:
    x, y = urlsplit(a), urlsplit(b)
    return (x.scheme, x.netloc, x.path) == (y.scheme, y.netloc, y.path)


def statuses(url: str, responses: dict[str, list]) -> tuple[object, list]:
    """(status of this exact URL, all statuses for the same file in request order)."""
    exact = responses.get(url, [])
    same_file = [s for u, seen in responses.items() if _same_file(u, url) for s in seen]
    return (exact[-1] if exact else None), same_file
