"""Secrets: gitleaks over the full git history of each public repo. Pass on zero findings."""

from __future__ import annotations

import json
import shutil
import subprocess
import tempfile
from pathlib import Path

from ..context import TargetUnavailable
from ..result import failed, passed

NAME = "secrets"


def run_gitleaks(ctx, source: Path) -> list[dict]:
    """Run gitleaks and return its findings (secrets redacted). Raises RuntimeError."""
    binary = shutil.which(ctx.gitleaks_bin) or (ctx.gitleaks_bin if Path(ctx.gitleaks_bin).is_file() else None)
    if not binary:
        raise RuntimeError(f"gitleaks not found ({ctx.gitleaks_bin}); install it or set GITLEAKS_BIN")
    mode = "git" if (source / ".git").exists() else "dir"
    with tempfile.TemporaryDirectory() as tmp:
        report = Path(tmp) / "gitleaks.json"
        cmd = [
            binary, mode, str(source),
            "--redact", "--no-banner", "--exit-code", "0", "--log-level", "error",
            "--report-format", "json", "--report-path", str(report),
        ]
        proc = subprocess.run(cmd, capture_output=True, text=True, timeout=900)
        if proc.returncode != 0:
            raise RuntimeError(f"gitleaks exited {proc.returncode}: {proc.stderr.strip()[-300:]}")
        if not report.exists():
            return []
        return json.loads(report.read_text() or "[]")


def run(ctx, repo):
    try:
        source = ctx.repo_dir(repo)
    except TargetUnavailable as exc:
        return failed(NAME, repo.name, str(exc))
    try:
        findings = run_gitleaks(ctx, source)
    except (RuntimeError, subprocess.TimeoutExpired, json.JSONDecodeError) as exc:
        return failed(NAME, repo.name, f"scan did not complete: {exc}")
    scope = "working tree (local path)" if ctx.is_local(repo) and not (source / ".git").exists() else "full git history"
    if not findings:
        return passed(NAME, repo.name, f"0 findings, {scope}", [f"gitleaks scanned {scope} of {repo.slug}"])
    evidence = [
        f"{f.get('RuleID')} in {f.get('File')}:{f.get('StartLine')}"
        + (f" (commit {str(f.get('Commit'))[:8]})" if f.get("Commit") else "")
        for f in findings
    ]
    return failed(NAME, repo.name, f"{len(findings)} finding(s), {scope}", evidence, findings=len(findings))
