"""Links: every URL in READMEs, sites and CVs opens without a login.

Pass when each URL answers 200 within the timeout and no redirect ends on a login page.

settings.link_exceptions in targets.yaml lists hosts that refuse requests from cloud
runners. On a GitHub Actions runner only, a listed status from a listed host is reported
as "not verified from CI" with its reason instead of failing. Run locally, the same URL
still has to return 200.

github.com rate-limits anonymous page requests from shared runners. When a repository,
file or folder URL on github.com still answers 429 after the retries, the check asks the
GitHub API instead and passes only if the repository is public and the file or folder
exists at that ref. Other github.com URLs that stay at 429 still fail.
"""

from __future__ import annotations

import os
import re
import threading
import time
from concurrent.futures import ThreadPoolExecutor
from urllib.parse import urlparse

import requests

from ..context import Context, TargetUnavailable
from ..extract import docx_links, markdown_links, normalize_for_fetch, resolve_readme_link
from ..result import failed, passed, skipped
from ._common import find_readme

NAME = "links"

LOGIN_PATTERNS = [
    r"^https://github\.com/login",
    r"^https://github\.com/session",
    r"^https://accounts\.google\.com/",
    r"^https://login\.microsoftonline\.com/",
    r"^https://www\.linkedin\.com/(authwall|login|uas/login)",
    r"[/?](login|signin|sign-in|sign_in|log-in)\b",
    r"ServiceLogin",
]
SKIP_SCHEMES = ("mailto:", "tel:", "javascript:", "data:")


def is_login_url(url: str) -> bool:
    return any(re.search(p, url, re.I) for p in LOGIN_PATTERNS)


_host_locks: dict[str, threading.Semaphore] = {}
_host_locks_guard = threading.Lock()
RATE_LIMITED_HOSTS = {"github.com": 2, "raw.githubusercontent.com": 2}


def _host_lock(url: str) -> threading.Semaphore:
    host = urlparse(url).netloc.lower()
    with _host_locks_guard:
        if host not in _host_locks:
            _host_locks[host] = threading.Semaphore(RATE_LIMITED_HOSTS.get(host, 4))
        return _host_locks[host]


def check_url(ctx: Context, url: str, sleep=time.sleep) -> tuple[bool, str]:
    """(ok, evidence line) for one URL.

    Retries once on connection errors and up to three times on 429 (rate limit),
    waiting as long as Retry-After asks (capped at 30 s).
    """
    last_error = ""
    attempts = 0
    while attempts < 4:
        attempts += 1
        try:
            with _host_lock(url):
                resp = ctx.fetch(url)
        except requests.Timeout:
            last_error = f"timeout after {ctx.config.link_timeout:g} s"
            if attempts >= 2:
                break
            continue
        except requests.RequestException as exc:
            last_error = f"{type(exc).__name__}"
            if attempts >= 2:
                break
            continue
        if resp.status_code == 429 and attempts < 4:
            try:
                wait = float(resp.headers.get("Retry-After", ""))
            except ValueError:
                wait = 5.0 * attempts
            sleep(min(max(wait, 1.0), 30.0))
            continue
        if resp.status_code == 429 and urlparse(url).netloc.lower() == "github.com":
            return verify_via_github_api(ctx, url)
        final = resp.url or url
        redirected = normalize_for_fetch(final).rstrip("/") != normalize_for_fetch(url).rstrip("/")
        if redirected and is_login_url(final):
            return False, f"{resp.status_code} {url} -> redirects to login page {final}"
        if resp.status_code != 200:
            return False, f"{resp.status_code} {url}" + (f" -> {final}" if redirected else "")
        return True, f"200 {url}" + (f" -> {final}" if redirected else "")
    return False, f"ERR {url}: {last_error}"


GITHUB_PAGE = re.compile(
    r"^https://github\.com/(?P<owner>[\w.-]+)/(?P<repo>[\w.-]+)"
    r"(?:/(?P<kind>blob|tree)/(?P<ref>[^/?#]+)(?:/(?P<path>[^?#]*))?)?/?(?:[?#].*)?$"
)


def verify_via_github_api(ctx: Context, url: str) -> tuple[bool, str]:
    """Fallback for a github.com page that stays rate-limited (429).

    Passes only if the repository is public and, for blob/tree URLs, the path exists
    at that ref. The API call may use a token, so the public flag is checked explicitly.
    """
    m = GITHUB_PAGE.match(url)
    if not m:
        return False, f"429 {url} (rate-limited; no API fallback for this URL)"
    owner, repo = m["owner"], m["repo"]
    meta = ctx.github_api(f"repos/{owner}/{repo}")
    if meta.status_code != 200 or meta.json().get("private", True):
        return False, f"429 {url} (rate-limited; API: repository not public or not found)"
    if m["kind"]:
        contents = ctx.github_api(f"repos/{owner}/{repo}/contents/{m['path'] or ''}",
                                  params={"ref": m["ref"]})
        if contents.status_code != 200:
            return False, f"429 {url} (rate-limited; API: {contents.status_code} for path)"
    return True, f"200 {url} (page rate-limited with 429; verified via GitHub API: public, exists)"


def on_ci_runner() -> bool:
    return os.environ.get("GITHUB_ACTIONS") == "true"


def link_exception(ctx: Context, url: str, line: str) -> dict | None:
    """The link_exceptions entry that covers this failed URL on a CI runner, if any."""
    if not on_ci_runner():
        return None
    status = line.split(" ", 1)[0]
    if not status.isdigit():
        return None
    host = urlparse(url).netloc.lower()
    for entry in ctx.config.link_exceptions:
        if host == entry["host"] and int(status) in entry["status"]:
            return entry
    return None


def collect(ctx: Context, target) -> tuple[list[str], list[str]]:
    """(urls to fetch, evidence notes). Raises TargetUnavailable."""
    notes: list[str] = []
    if target.kind == "repo":
        path, text = find_readme(ctx, target)
        root = ctx.repo_dir(target)
        rel_readme = path.relative_to(root).as_posix()
        urls = []
        for link in markdown_links(text):
            if ctx.is_local(target) and not re.match(r"(?i)^[a-z][a-z0-9+.-]*:|^#|^//", link):
                # Local checkout: relative links must exist in the working tree.
                local = (path.parent / link.split("#")[0].split("?")[0]).resolve()
                if not local.exists():
                    urls.append(f"local-missing:{link}")
                else:
                    notes.append(f"exists {link} (local file)")
                continue
            urls.append(resolve_readme_link(link, target.slug, rel_readme))
        return urls, notes
    if target.kind == "site":
        page = ctx.render(target)
        if page.error:
            raise TargetUnavailable(f"render failed: {page.error}")
        return [target.url, *page.links], notes
    if target.kind == "cv":
        path, _explicit = ctx.cv_path(target)
        return docx_links(path), notes
    return [], notes


def run(ctx: Context, target):
    if target.kind == "cv":
        from .cv_content import cv_precheck

        pre = cv_precheck(ctx, target, NAME)
        if pre is not None:
            return pre
    try:
        urls, notes = collect(ctx, target)
    except TargetUnavailable as exc:
        return failed(NAME, target.name, str(exc))

    evidence: list[str] = []
    bad: list[str] = []
    to_fetch: list[str] = []
    for url in urls:
        if url.startswith("local-missing:"):
            bad.append(f"MISSING local file {url.split(':', 1)[1]}")
            continue
        if url.lower().startswith(SKIP_SCHEMES):
            evidence.append(f"skip {url} (not fetchable)")
            continue
        if urlparse(url).scheme not in ("http", "https"):
            bad.append(f"INVALID {url}")
            continue
        clean = normalize_for_fetch(url)
        if clean not in to_fetch:
            to_fetch.append(clean)

    if not to_fetch and not bad:
        return skipped(NAME, target.name, "no links found", notes + evidence)

    with ThreadPoolExecutor(max_workers=8) as pool:
        results = list(pool.map(lambda u: check_url(ctx, u), to_fetch))
    ok_lines = [line for ok, line in results if ok]
    unverified: list[str] = []
    for url, (ok, line) in zip(to_fetch, results):
        if ok:
            continue
        entry = link_exception(ctx, url, line)
        if entry:
            unverified.append(f"{line} (not verified from CI: {entry['reason']})")
        else:
            bad.append(line)

    total = len(to_fetch) + len([b for b in bad if b.startswith(("MISSING", "INVALID"))])
    evidence = bad + unverified + ok_lines + notes + evidence
    data = {"checked": len(to_fetch), "broken": len(bad)}
    if unverified:
        data["unverified"] = len(unverified)
    if bad:
        return failed(NAME, target.name, f"{len(bad)} of {total} links broken", evidence, **data)
    if unverified:
        summary = (f"{total - len(unverified)} of {total} links return 200; "
                   f"{len(unverified)} not verified from CI (link_exceptions)")
        return passed(NAME, target.name, summary, evidence, **data)
    return passed(NAME, target.name, f"all {total} links return 200", evidence, **data)
