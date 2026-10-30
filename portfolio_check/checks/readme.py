"""README contract: the first screen of a README answers what a reviewer asks first.

The first screen is the first `readme_first_screen_lines` lines (default 60). It must hold:
a one-line problem statement, a screenshot or GIF, a "Try it" link or command,
"My role", the tech stack and the limitations.
"""

from __future__ import annotations

import re

from ..context import TargetUnavailable
from ..result import failed, passed
from ._common import find_readme

NAME = "readme"

TRY_RE = re.compile(r"^\W*(try it|ausprobieren|quick ?start|(live )?demo|run it)\b", re.I)
ROLE_RE = re.compile(r"\b(my role|meine rolle)\b", re.I)
STACK_RE = re.compile(r"\b(tech stack|stack|built with|technolog\w*)\b", re.I)
LIMITS_RE = re.compile(
    r"\b(limitations?|known issues|what failed|what did not work|caveats|grenzen|einschränkungen|was nicht funktioniert)\b",
    re.I,
)
IMAGE_RE = re.compile(r"!\[[^\]]*\]\(\s*<?([^)\s>]+)|<img\b[^>]*\bsrc\s*=\s*[\"']([^\"']+)", re.I)
IMAGE_EXT_RE = re.compile(r"\.(png|jpe?g|gif|webp|svg|avif)(\?.*)?$", re.I)
BADGE_RE = re.compile(r"shields\.io|badge|badgen\.net|/actions/workflows/", re.I)
HEADING_RE = re.compile(r"^\s{0,3}(#{1,6})\s+(.*?)\s*#*\s*$")
HTML_HEADING_RE = re.compile(r"<h([1-6])[^>]*>(.*?)</h\1>", re.I)
BOLD_LABEL_RE = re.compile(r"^\s*(?:[-*]\s+)?\*\*([^*]+)\*\*")
COLON_LABEL_RE = re.compile(r"^\s*(?:[-*]\s+)?([A-Za-zÄÖÜäöü][\w ÄÖÜäöü-]{1,30}):\s")


def labels(lines: list[str]) -> list[tuple[int, str, bool]]:
    """(line index, label text, is_heading) for headings and bold or colon labels."""
    out = []
    in_fence = False
    for i, line in enumerate(lines):
        if line.lstrip().startswith(("```", "~~~")):
            in_fence = not in_fence
            continue
        if in_fence:
            continue
        m = HEADING_RE.match(line)
        if m:
            if len(m.group(1)) > 1:  # the H1 is the project title, not a section
                out.append((i, m.group(2), True))
            continue
        m = HTML_HEADING_RE.search(line)
        if m:
            out.append((i, re.sub(r"<[^>]+>", "", m.group(2)), True))
            continue
        m = BOLD_LABEL_RE.match(line) or COLON_LABEL_RE.match(line)
        if m:
            out.append((i, m.group(1), False))
    return out


def problem_statement(lines: list[str]) -> tuple[int, str] | None:
    """First prose paragraph: not a heading, image, badge, HTML, list, table or code."""
    in_fence = False
    para: list[str] = []
    start = 0
    for i, raw in enumerate(lines):
        line = raw.strip()
        if line.startswith(("```", "~~~")):
            in_fence = not in_fence
            if para:
                break
            continue
        if in_fence:
            continue
        prose = bool(line) and not (
            line.startswith(("#", "!", "[!", "<", "|", "- ", "* ", "+ ", "---", "===", "[//]"))
            or re.match(r"^\d+\.\s", line)
            or re.match(r"^\[[^\]]+\]:\s", line)
        )
        if prose:
            if not para:
                start = i
            para.append(line.lstrip("> ").strip())
        elif para:
            break
    if not para:
        return None
    return start, " ".join(para)


def section_body(lines: list[str], start: int) -> str:
    """Text from a label line to the next heading."""
    body = [lines[start]]
    for line in lines[start + 1 :]:
        if HEADING_RE.match(line) or HTML_HEADING_RE.search(line):
            break
        body.append(line)
    return "\n".join(body)


def evaluate(text: str, first_screen: int = 60) -> tuple[list[str], list[str]]:
    """(missing items, evidence lines)."""
    lines = text.splitlines()[:first_screen]
    missing: list[str] = []
    evidence: list[str] = []

    ps = problem_statement(lines)
    if ps and 15 <= len(ps[1]) <= 300:
        evidence.append(f"problem statement, line {ps[0] + 1}: {ps[1][:120]!r}")
    elif ps:
        missing.append(f"one-line problem statement (first paragraph has {len(ps[1])} chars, want 15-300)")
    else:
        missing.append("one-line problem statement")

    visual = None
    for i, line in enumerate(lines):
        for m in IMAGE_RE.finditer(line):
            src = m.group(1) or m.group(2)
            if IMAGE_EXT_RE.search(src) and not BADGE_RE.search(src):
                visual = (i, src)
                break
        if visual:
            break
    if visual:
        evidence.append(f"screenshot/GIF, line {visual[0] + 1}: {visual[1]}")
    else:
        missing.append("screenshot or GIF (badges do not count)")

    found = {"try": None, "role": None, "stack": None, "limits": None}
    for i, label, _is_heading in labels(lines):
        for key, regex in (("try", TRY_RE), ("role", ROLE_RE), ("stack", STACK_RE), ("limits", LIMITS_RE)):
            if found[key] is None and regex.search(label):
                if key == "try":
                    body = section_body(lines, i)
                    if not (re.search(r"https?://", body) or "```" in body or re.search(r"`[^`]+`", body)):
                        continue
                found[key] = (i, label)

    names = {
        "try": '"Try it" link or demo command',
        "role": '"My role"',
        "stack": "tech stack",
        "limits": "limitations",
    }
    for key, hit in found.items():
        if hit:
            evidence.append(f"{names[key]}, line {hit[0] + 1}: {hit[1].strip()!r}")
        else:
            missing.append(names[key])
    return missing, evidence


def run(ctx, repo):
    try:
        path, text = find_readme(ctx, repo)
    except TargetUnavailable as exc:
        return failed(NAME, repo.name, str(exc))
    n = ctx.config.readme_first_screen_lines
    missing, evidence = evaluate(text, n)
    where = f"{path.name}, first {n} lines"
    if missing:
        return failed(
            NAME,
            repo.name,
            f"{len(missing)} of 6 items missing in {where}",
            [f"missing: {m}" for m in missing] + evidence,
            missing=missing,
        )
    return passed(NAME, repo.name, f"all 6 items present in {where}", evidence)
