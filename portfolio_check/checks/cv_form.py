"""CV form: page count after LibreOffice PDF export, no nested tables, LanguageTool clean.

Pass when the PDF has at most `max_pages` pages (cv_rules.yaml, default 2), the document
has no table inside a table, and LanguageTool reports no spelling or grammar issue
outside the allowlist of technical terms.
"""

from __future__ import annotations

import os
import re
import shutil
import subprocess
import tempfile
import zipfile
from pathlib import Path

import requests

from ..extract import docx_has_nested_tables, docx_text
from ..result import failed, passed
from .cv_content import cv_precheck

NAME = "cv-form"
CHUNK = 15_000
URLISH = re.compile(r"(https?://|www\.|@|\.com\b|\.de\b|\.app\b)", re.I)
BULLET_ONLY = re.compile(r"^[•·▪◦*–-]$")


def spellcheck_text(text: str) -> str:
    """One block per paragraph and per tab-separated column, blank line between blocks.

    LanguageTool reads a single newline or a tab as a space, so a heading followed by a
    capitalised word ("PROJECTS Released") or a date column next to a name would look
    like one sentence. A blank line ends the sentence, the way a reader sees the layout.
    """
    blocks = []
    for line in text.split("\n"):
        for part in line.split("\t"):
            part = part.strip()
            if part and not BULLET_ONLY.match(part):
                blocks.append(part)
    return "\n\n".join(blocks)


def _in_span(chunk: str, offset: int, length: int, spans: list[str]) -> bool:
    for span in spans:
        start = chunk.find(span)
        while start != -1:
            if start <= offset and offset + length <= start + len(span):
                return True
            start = chunk.find(span, start + 1)
    return False


# Microsoft Office fonts and the open fonts LibreOffice should use when the original is
# not installed (Linux, CI). Carlito, Caladea and Liberation have the same character
# widths as the fonts they replace. Calibri Light has no open twin; Carlito is close.
# Without this map fontconfig picks any sans font for "Calibri Light" (Inter on one
# machine, 30 Oct 2026), which made a 2-page CV export as 3 pages.
METRIC_STANDINS = {
    "Calibri": "Carlito",
    "Calibri Light": "Carlito",
    "Cambria": "Caladea",
    "Arial": "Liberation Sans",
    "Times New Roman": "Liberation Serif",
    "Courier New": "Liberation Mono",
}
# Latin text only: w:ascii / w:hAnsi on runs and styles, a:latin in the theme. The
# theme also names a font for every other script (Thai, Hangul, ...), which a Latin CV
# never renders and which would bury the report in irrelevant substitutions.
RUN_FONT = re.compile(r'w:(?:ascii|hAnsi)="([^"]+)"')
THEME_FONT = re.compile(r'<a:latin typeface="([^"]+)"')
FONT_PARTS = ("word/document.xml", "word/styles.xml", "word/header1.xml", "word/footer1.xml")


def docx_fonts(path: Path) -> set[str]:
    """Latin font families the document asks for: runs, styles and the theme."""
    fonts: set[str] = set()
    with zipfile.ZipFile(path) as z:
        names = set(z.namelist())
        for part in FONT_PARTS:
            if part in names:
                fonts.update(RUN_FONT.findall(z.read(part).decode("utf-8", "replace")))
        if "word/theme/theme1.xml" in names:
            fonts.update(THEME_FONT.findall(z.read("word/theme/theme1.xml").decode("utf-8", "replace")))
    return {f for f in fonts if f.strip()}


def fontconfig_file(directory: Path) -> Path:
    """A fontconfig file that adds each stand-in after its original font.

    `accept` keeps the original when it is installed (a Windows or Mac machine with
    Office) and uses the stand-in only when it is missing.
    """
    aliases = "".join(
        f"<alias binding=\"same\"><family>{src}</family><accept><family>{dst}</family></accept></alias>"
        for src, dst in METRIC_STANDINS.items()
    )
    conf = directory / "fonts.conf"
    conf.write_text(
        '<?xml version="1.0"?><!DOCTYPE fontconfig SYSTEM "fonts.dtd"><fontconfig>'
        '<include ignore_missing="yes">/etc/fonts/fonts.conf</include>'
        f"{aliases}</fontconfig>",
        encoding="utf-8",
    )
    return conf


def _fc_match(family: str, conf: Path) -> str | None:
    """The family fontconfig renders for `family`, or None without fontconfig."""
    binary = shutil.which("fc-match")
    if not binary:
        return None
    proc = subprocess.run(
        [binary, "-f", "%{family[0]}", family], capture_output=True, text=True, timeout=20,
        env={**os.environ, "FONTCONFIG_FILE": str(conf)},
    )
    return proc.stdout.strip() or None


def font_notes(docx: Path) -> tuple[list[str], list[str]]:
    """(stand-ins used, fonts with no metric stand-in) for the fonts the CV asks for.

    Empty lists where fontconfig is not available (Windows), since there the installed
    Office fonts are used as they are.
    """
    standins, fallbacks = [], []
    with tempfile.TemporaryDirectory() as tmp:
        conf = fontconfig_file(Path(tmp))
        for font in sorted(docx_fonts(docx)):
            got = _fc_match(font, conf)
            if got is None or got.lower() == font.lower():
                continue
            if got == METRIC_STANDINS.get(font):
                standins.append(f"{font} → {got}")
            else:
                fallbacks.append(f"{font} → {got}")
    return standins, fallbacks


def pdf_pages(ctx, docx: Path) -> tuple[int, Path]:
    """Convert with LibreOffice headless and count pages. Raises RuntimeError."""
    binary = shutil.which(ctx.soffice_bin) or shutil.which("libreoffice")
    if not binary:
        raise RuntimeError("LibreOffice (soffice) not found; install libreoffice-writer")
    outdir = ctx.out_dir / "cv-pdf"
    outdir.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory() as profile:
        env = {**os.environ, "FONTCONFIG_FILE": str(fontconfig_file(Path(profile)))}
        proc = subprocess.run(
            [binary, f"-env:UserInstallation=file://{profile}/lo", "--headless",
             "--convert-to", "pdf", "--outdir", str(outdir), str(docx)],
            capture_output=True, text=True, timeout=240, env=env,
        )
    pdf = outdir / (docx.stem + ".pdf")
    if proc.returncode != 0 or not pdf.exists():
        raise RuntimeError(f"PDF export failed: {(proc.stderr or proc.stdout).strip()[-300:]}")
    from pypdf import PdfReader

    return len(PdfReader(str(pdf)).pages), pdf


def _chunks(text: str) -> list[str]:
    out, cur = [], ""
    for para in text.split("\n\n"):
        if len(cur) + len(para) + 2 > CHUNK and cur:
            out.append(cur)
            cur = ""
        cur += para + "\n\n"
    if cur.strip():
        out.append(cur)
    return out


def language_issues(ctx, text: str, language: str, allowlist: list[str], ignore_rules: list[str]) -> list[str]:
    """LanguageTool matches that are not allowlisted. Raises RuntimeError.

    Allowlist lines are single terms (case-insensitive), or "span: TEXT" to exempt an exact
    passage such as a citation in another language.
    """
    spans = [a[5:].strip() for a in allowlist if a.startswith("span:")]
    allowed = {a.lower() for a in allowlist if not a.startswith("span:")}
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
            if _in_span(chunk, m["offset"], m["length"], spans):
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

    standins, fallbacks = font_notes(path)
    if standins:
        info.append("fonts not installed, rendered with metric stand-ins: " + ", ".join(standins))
    if fallbacks:
        info.append(
            "fonts not installed and without a metric stand-in: " + ", ".join(fallbacks)
            + "; the page count may differ from Word"
        )

    try:
        pages, pdf = pdf_pages(ctx, path)
        line = f"{pages} page(s) as PDF ({pdf.relative_to(ctx.out_dir)}), limit {max_pages}"
        if pages > max_pages and fallbacks:
            line += " (may be caused by the substituted fonts above; check in Word)"
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
            ctx, spellcheck_text(docx_text(path)), cv.language, ctx.config.allowlist, lt.get("ignore_rules", [])
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
