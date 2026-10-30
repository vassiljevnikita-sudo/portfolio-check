from __future__ import annotations

import json
from datetime import datetime, timezone
from pathlib import Path

from .result import FAIL, PASS, SKIP, CheckResult

MD_EVIDENCE_LIMIT = 40
ICON = {PASS: "PASS", FAIL: "FAIL", SKIP: "SKIP"}


def _cell(text: str) -> str:
    return text.replace("|", "\\|").replace("\n", " ")


def counts(results: list[CheckResult]) -> dict[str, int]:
    return {s: sum(r.status == s for r in results) for s in (PASS, FAIL, SKIP)}


def write_reports(results: list[CheckResult], out_dir: Path, meta: dict) -> tuple[Path, Path]:
    out_dir.mkdir(parents=True, exist_ok=True)
    started = meta.get("started") or datetime.now(timezone.utc).isoformat(timespec="seconds")
    c = counts(results)
    overall = FAIL if c[FAIL] else PASS

    payload = {
        "generated": started,
        "overall": overall,
        "counts": c,
        **{k: v for k, v in meta.items() if k != "started"},
        "results": [r.to_dict() for r in results],
    }
    json_path = out_dir / "report.json"
    json_path.write_text(json.dumps(payload, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")

    lines = [
        "# Portfolio check report",
        "",
        f"Run {started} · overall **{overall.upper()}** · "
        f"{c[PASS]} pass, {c[FAIL]} fail, {c[SKIP]} skipped",
        "",
        "| Check | Target | Result | Summary |",
        "| --- | --- | --- | --- |",
    ]
    for r in results:
        lines.append(f"| {r.check} | {r.target} | {ICON[r.status]} | {_cell(r.summary)} |")
    lines += ["", "## Evidence", ""]
    for r in results:
        lines.append(f"### {r.check} · {r.target} · {ICON[r.status]}")
        lines.append("")
        lines.append(r.summary)
        lines.append("")
        for ev in r.evidence[:MD_EVIDENCE_LIMIT]:
            lines.append(f"- {ev}")
        if len(r.evidence) > MD_EVIDENCE_LIMIT:
            lines.append(f"- ... {len(r.evidence) - MD_EVIDENCE_LIMIT} more lines in report.json")
        lines.append("")
    md_path = out_dir / "report.md"
    md_path.write_text("\n".join(lines), encoding="utf-8")
    return md_path, json_path
