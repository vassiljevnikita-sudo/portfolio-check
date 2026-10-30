import json
import subprocess

import pytest

from portfolio_check.checks import secrets
from portfolio_check.result import FAIL, PASS


@pytest.fixture
def repo(ctx, tmp_path):
    d = tmp_path / "repo"
    (d / ".git").mkdir(parents=True)
    ctx.local_paths["demo-repo"] = d
    return ctx.config.repos[0]


def _fake_gitleaks(monkeypatch, findings, returncode=0):
    seen = {}

    def fake_run(cmd, **kwargs):
        seen["cmd"] = cmd
        report = cmd[cmd.index("--report-path") + 1]
        with open(report, "w") as fh:
            json.dump(findings, fh)
        return subprocess.CompletedProcess(cmd, returncode, "", "")

    monkeypatch.setattr(secrets.shutil, "which", lambda name: "/usr/bin/gitleaks")
    monkeypatch.setattr(secrets.subprocess, "run", fake_run)
    return seen


def test_secrets_pass_on_zero_findings(ctx, repo, monkeypatch):
    seen = _fake_gitleaks(monkeypatch, [])
    result = secrets.run(ctx, repo)
    assert result.status == PASS
    assert seen["cmd"][1] == "git" and "--redact" in seen["cmd"]


def test_secrets_fail_and_report_location_not_secret(ctx, repo, monkeypatch):
    finding = {"RuleID": "generic-api-key", "File": "config.py", "StartLine": 3, "Commit": "abcdef1234567", "Secret": "REDACTED"}
    _fake_gitleaks(monkeypatch, [finding])
    result = secrets.run(ctx, repo)
    assert result.status == FAIL
    assert result.evidence == ["generic-api-key in config.py:3 (commit abcdef12)"]


def test_secrets_fail_when_gitleaks_missing(ctx, repo, monkeypatch):
    monkeypatch.setattr(secrets.shutil, "which", lambda name: None)
    ctx.gitleaks_bin = "/nonexistent/gitleaks"
    result = secrets.run(ctx, repo)
    assert result.status == FAIL and "gitleaks not found" in result.summary
