"""CV content: read the .docx and compare it with cv_rules.yaml.

Pass when all required sections and keywords are present, every claim phrase from the
claim ledger matches, and no forbidden phrase appears.
"""

from __future__ import annotations

import re

from ..extract import docx_paragraphs, docx_text
from ..result import failed, passed, skipped

NAME = "cv-content"


def cv_precheck(ctx, cv, check_name):
    """Return a skip/fail result when the CV file is not available, else None."""
    path, explicit = ctx.cv_path(cv)
    if path is not None and path.is_file():
        return None
    if explicit or ctx.require_cv:
        return failed(check_name, cv.name, f"file missing: {path}")
    return skipped(
        check_name,
        cv.name,
        "skipped: no CV path given",
        [
            "not a pass: this check did not run",
            f"default path {cv.path} not present; pass --{cv.name} PATH or set {cv.env or 'the env var'}",
        ],
    )


def _rules_for(rules: dict, key: str, lang: str) -> list:
    value = rules.get(key, [])
    if isinstance(value, dict):
        return list(value.get("common", [])) + list(value.get(lang, []))
    return list(value)


def evaluate(paragraphs: list[tuple[str, str]], text: str, rules: dict, lang: str):
    problems: list[str] = []
    info: list[str] = []
    text_l = text.lower()

    short = [(style, t.strip()) for style, t in paragraphs if t.strip() and len(t.strip()) <= 60]
    for section in _rules_for(rules, "required_sections", lang):
        names = section if isinstance(section, list) else [section]
        hit = next(
            (t for _s, t in short if any(n.lower() in t.lower() for n in names)),
            None,
        )
        if hit:
            info.append(f"section {names[0]!r} found: {hit!r}")
        else:
            problems.append(f"missing section: {' / '.join(names)}")

    for kw in _rules_for(rules, "keywords", lang):
        if kw.lower() not in text_l:
            problems.append(f"missing keyword: {kw!r}")
    present_kw = [kw for kw in _rules_for(rules, "keywords", lang) if kw.lower() in text_l]
    if present_kw:
        info.append("keywords found: " + ", ".join(present_kw))

    for claim in rules.get("claims", []):
        patterns = claim.get(lang, [])
        if not patterns:
            continue
        missing = [p for p in patterns if not re.search(p, text, re.I)]
        if missing:
            problems.append(f"claim {claim['id']!r} not matched: {missing}")
        else:
            info.append(f"claim {claim['id']!r} matched")

    for phrase in _rules_for(rules, "forbidden_phrases", lang):
        if phrase.lower() in text_l:
            problems.append(f"forbidden phrase present: {phrase!r}")
    return problems, info


def run(ctx, cv):
    pre = cv_precheck(ctx, cv, NAME)
    if pre is not None:
        return pre
    path, _ = ctx.cv_path(cv)
    try:
        paragraphs = docx_paragraphs(path)
        text = docx_text(path)
    except Exception as exc:  # noqa: BLE001
        return failed(NAME, cv.name, f"cannot read {path.name}: {type(exc).__name__}: {exc}")
    problems, info = evaluate(paragraphs, text, ctx.config.cv_rules, cv.rules_language)
    if problems:
        return failed(NAME, cv.name, f"{len(problems)} problem(s) in {path.name}", problems + info)
    return passed(NAME, cv.name, f"sections, keywords and claims present in {path.name}", info)
