"""Employer denylist: case-insensitive substring search for private terms.

Terms come from the DENYLIST environment variable (one term per line) or a local file
given with --denylist-file. They are never written to the report: a hit names the term
by its position in the list (term #3), plus file, line or commit.

Repos: every tracked text file at HEAD, text inside .docx/.xlsx/.pptx, file paths, and
the full history (patches, commit messages, author names). Sites: the rendered page
plus same-origin pages it links to.
"""

from __future__ import annotations

import subprocess
from pathlib import Path
from urllib.parse import urlparse

from ..context import TargetUnavailable
from ..extract import office_zip_text
from ..result import failed, passed

NAME = "denylist"
OFFICE_EXT = {".docx", ".docm", ".dotx", ".xlsx", ".xlsm", ".pptx"}
MAX_FILE_BYTES = 20 * 1024 * 1024


def load_terms(raw: str) -> list[str]:
    return [t.strip() for t in raw.splitlines() if t.strip() and not t.strip().startswith("#")]


def find_hits(text: str, terms: list[str]) -> list[tuple[int, int, bool]]:
    """(term number starting at 1, line number, whole_word) for each hit."""
    hits = []
    for lineno, line in enumerate(text.splitlines(), 1):
        low = line.lower()
        for n, term in enumerate(terms, 1):
            t = term.lower()
            start = low.find(t)
            while start != -1:
                before = low[start - 1] if start > 0 else " "
                after = low[start + len(t)] if start + len(t) < len(low) else " "
                whole = not (before.isalnum() or after.isalnum())
                hits.append((n, lineno, whole))
                start = low.find(t, start + 1)
    return hits


def _fmt(n: int, where: str, whole: bool) -> str:
    kind = "" if whole else " (inside a longer word)"
    return f"term #{n} in {where}{kind}"


def _tracked_files(root: Path) -> list[Path]:
    if (root / ".git").exists():
        proc = subprocess.run(
            ["git", "-C", str(root), "ls-files", "-co", "--exclude-standard", "-z"],
            capture_output=True, text=True, check=True,
        )
        return [root / p for p in proc.stdout.split("\0") if p]
    return [p for p in root.rglob("*") if p.is_file() and ".git" not in p.parts]


def scan_tree(root: Path, terms: list[str]) -> list[str]:
    out: list[str] = []
    for path in _tracked_files(root):
        rel = path.relative_to(root).as_posix()
        for n, _line, whole in find_hits(rel, terms):
            out.append(_fmt(n, f"file path {rel}", whole))
        if not path.is_file() or path.stat().st_size > MAX_FILE_BYTES:
            continue
        if path.suffix.lower() in OFFICE_EXT:
            for n, _line, whole in find_hits(office_zip_text(path), terms):
                out.append(_fmt(n, f"{rel} (document text)", whole))
            continue
        data = path.read_bytes()
        if b"\0" in data[:8192]:
            continue
        for n, line, whole in find_hits(data.decode("utf-8", "replace"), terms):
            out.append(_fmt(n, f"{rel}:{line}", whole))
    return out


def scan_history(root: Path, terms: list[str]) -> list[str]:
    """Search every patch, commit message and author line in all refs."""
    if not (root / ".git").exists():
        return []
    proc = subprocess.Popen(
        ["git", "-C", str(root), "log", "--all", "-p", "--no-color", "--text",
         "--format=@@commit %H%n%an <%ae>%n%B"],
        stdout=subprocess.PIPE, text=True, errors="replace",
    )
    out: set[str] = set()
    commit, current_file = "", ""
    assert proc.stdout is not None
    for line in proc.stdout:
        if line.startswith("@@commit "):
            commit, current_file = line.split()[1][:8], "commit message/author"
            continue
        if line.startswith("diff --git "):
            current_file = line.rsplit(" b/", 1)[-1].strip()
            for n, _l, whole in find_hits(current_file, terms):
                out.add(_fmt(n, f"history commit {commit} (file path {current_file})", whole))
            continue
        for n, _l, whole in find_hits(line, terms):
            out.add(_fmt(n, f"history commit {commit} ({current_file})", whole))
    proc.wait()
    return sorted(out)


def scan_site(ctx, site, terms: list[str]) -> list[str]:
    page = ctx.render(site)
    if page.error:
        raise TargetUnavailable(f"render failed: {page.error}")
    out: list[str] = []
    for n, line, whole in find_hits(page.text, terms):
        out.append(_fmt(n, f"rendered text of {site.url}, line {line}", whole))
    for n, line, whole in find_hits(page.html, terms):
        out.append(_fmt(n, f"rendered HTML of {site.url}, line {line}", whole))
    origin = urlparse(page.final_url or site.url).netloc
    seen = {site.url.split("#")[0], (page.final_url or "").split("#")[0]}
    for link in page.links:
        url = link.split("#")[0]
        if urlparse(url).netloc != origin or url in seen:
            continue
        seen.add(url)
        try:
            status, body = ctx.get_text(url)
        except Exception:  # noqa: BLE001 - broken links are the links check's job
            continue
        for n, line, whole in find_hits(body, terms):
            out.append(_fmt(n, f"{url}, line {line}", whole))
    return sorted(set(out))


def run(ctx, target):
    terms = ctx.denylist
    if not terms:
        return failed(NAME, target.name, "no denylist provided (set DENYLIST or pass --denylist-file)")
    try:
        if target.kind == "repo":
            root = ctx.repo_dir(target)
            hits = scan_tree(root, terms) + scan_history(root, terms)
            scope = "HEAD files, file paths and full history"
        else:
            hits = scan_site(ctx, target, terms)
            scope = "rendered page and same-origin linked pages"
    except TargetUnavailable as exc:
        return failed(NAME, target.name, str(exc))
    if hits:
        return failed(NAME, target.name, f"{len(hits)} hit(s) for {len(terms)} terms ({scope})", hits, hits=len(hits))
    return passed(NAME, target.name, f"0 hits for {len(terms)} terms ({scope})")
