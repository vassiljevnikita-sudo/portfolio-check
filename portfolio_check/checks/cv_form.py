"""CV form: page count after LibreOffice PDF export, no nested tables, LanguageTool clean.

Pass when the PDF has at most `max_pages` pages (cv_rules.yaml, default 2), the document
has no table inside a table, and LanguageTool reports no spelling or grammar issue
outside the allowlist of technical terms.
"""

from __future__ import annotations

import re
import shutil
import subprocess
import tempfile
from pathlib import Path

import requests

from ..extract import docx_has_nested_tables, docx_text
from ..result import failed, passed
from .cv_content import cv_precheck

NAME = "cv-form"
CHUNK = 15_000
URLISH = re.compile(r"(https?://|www\.|@|\.com\b|\.de\b|\.app\b)", re.I)


def pdf_pages(ctx, docx: Path) -> tuple[int, Path]:
    """Convert with LibreOffice headless and count pages. Raises RuntimeError."""
    binary = shutil.which(ctx.soffice_bin) or shutil.which("libreoffice")
    if not binary:
        raise RuntimeError("LibreOffice (soffice) not found; install libreoffice-writer")
    outdir = ctx.out_dir / "cv-pdf"
    outdir.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory() as profile:
        proc = subprocess.run(
            [binary, f"-env:UserInstallation=file://{profile}", "--headless",
             "--convert-to", "pdf", "--outdir", str(outdir), str(docx)],
            capture_output=True, text=True, timeout=240,
        )
    pdf = outdir / (docx.stem + ".pdf")
    if proc.returncode != 0 or not pdf.exists():
        raise RuntimeError(f"PDF export failed: {(proc.stderr or proc.stdout).strip()[-300:]}")
    from pypdf import PdfReader

    return len(PdfReader(str(pdf)).pages), pdf


def _chunks(text: str) -> list[str]:
    out, cur = [], ""
    for para in text.split("\n"):
        if len(cur) + len(para) + 1 > CHUNK and cur:
            out.append(cur)
            cur = ""
        cur += para + "\n"
    if cur.strip():
        out.append(cur)
    return out


def language_issues(ctx, text: str, language: str, allowlist: list[str], ignore_rules: list[str]) -> list[str]:
    """LanguageTool matches that are not allowlisted. Raises RuntimeError."""
    allowed = {a.lower() for a in allowlist}
    issues: list[str] = []
    for chunk in _chunks(text):
        try:
            resp = ctx.http.post(
                ctx.languagetool_url,
                data={"text": chunk, "language": language, "level": "default"},
                timeout=60,
            )
        except requests.RequestException as exc:
            raise RuntimeError(f"LanguageTool unreachable: {type(exc).__name__}") from exc
        if resp.status_code != 200:
            raise RuntimeError(f"LanguageTool returned {resp.status_code}: {resp.text[:200]}")
        for m in resp.json().get("matches", []):
            token = chunk[m["offset"] : m["offset"] + m["length"]]
            rule = m.get("rule", {}).get("id", "?")
            if rule in ignore_rules:
                continue
            if token.lower().strip(".,;:()") in allowed or URLISH.search(token):
                continue
            context = m.get("context", {}).get("text", "").strip()
            issues.append(f"{rule}: {m.get('message', '')} [{token!r} in {context!r}]")
    return issues


def run(ctx, cv):
    pre = cv_precheck(ctx, cv, NAME)
    if pre is not None:
        return pre
    path, _ = ctx.cv_path(cv)
    rules = ctx.config.cv_rules
    max_pages = int(rules.get("max_pages", 2))
    problems: list[str] = []
    info: list[str] = []

    try:
        pages, pdf = pdf_pages(ctx, path)
        line = f"{pages} page(s) as PDF ({pdf.relative_to(ctx.out_dir)}), limit {max_pages}"
        (problems if pages > max_pages else info).append(line)
    except (RuntimeError, subprocess.TimeoutExpired) as exc:
        problems.append(f"page count unavailable: {exc}")

    nested = docx_has_nested_tables(path)
    if nested:
        problems.append(f"{nested} nested table(s); applicant-tracking software may not read them")
    else:
        info.append("no nested tables")

    lt = rules.get("languagetool", {})
    try:
        issues = language_issues(
            ctx, docx_text(path), cv.language, ctx.config.allowlist, lt.get("ignore_rules", [])
        )
        if issues:
            problems.append(f"LanguageTool ({cv.language}): {len(issues)} issue(s)")
            problems.extend(f"  {i}" for i in issues)
        else:
            info.append(f"LanguageTool ({cv.language}): 0 issues outside the allowlist")
    except RuntimeError as exc:
        problems.append(f"spelling check unavailable: {exc}")

    if problems:
        return failed(NAME, cv.name, f"{len([p for p in problems if not p.startswith('  ')])} problem(s) in {path.name}", problems + info)
    return passed(NAME, cv.name, f"{path.name}: page limit, structure and spelling OK", info)
