import json
import shutil

from portfolio_check.cli import main
from portfolio_check.config import load_config

from .conftest import FIXTURES, ROOT

TARGETS = """
repos:
  - name: good
    slug: someone/good
  - name: bad
    slug: someone/bad
  - name: profile
    slug: someone/someone
    skip_checks: [readme]
    skip_reason: profile page
"""


def _setup(tmp_path):
    (tmp_path / "targets.yaml").write_text(TARGETS)
    for name, fixture in (("good", "README_good.md"), ("bad", "README_bad.md")):
        (tmp_path / name).mkdir()
        shutil.copy(FIXTURES / fixture, tmp_path / name / "README.md")
    return ["--targets", str(tmp_path / "targets.yaml"), "--out", str(tmp_path / "out"),
            "--local", f"good={tmp_path / 'good'}", "--local", f"bad={tmp_path / 'bad'}"]


def test_single_check_single_target_exits_0_and_writes_reports(tmp_path):
    args = _setup(tmp_path)
    assert main([*args, "--check", "readme", "--target", "good"]) == 0
    report = json.loads((tmp_path / "out" / "report.json").read_text())
    assert report["overall"] == "pass"
    assert [r["target"] for r in report["results"]] == ["good"]
    assert "| readme | good | PASS |" in (tmp_path / "out" / "report.md").read_text()


def test_any_failure_exits_1(tmp_path):
    args = _setup(tmp_path)
    assert main([*args, "--check", "readme"]) == 1
    report = json.loads((tmp_path / "out" / "report.json").read_text())
    assert report["counts"] == {"pass": 1, "fail": 1, "skip": 1}
    assert "A skipped check did not run. It is not a pass." in (tmp_path / "out" / "report.md").read_text()


def test_unknown_check_or_target_exits_2(tmp_path):
    args = _setup(tmp_path)
    assert main([*args, "--check", "nope"]) == 2
    assert main([*args, "--check", "readme", "--target", "nope"]) == 2


def test_shipped_targets_file_loads():
    config = load_config(ROOT / "targets.yaml")
    names = {t.name for t in config.targets}
    assert {"complaint-dossier", "simple-and-light-research", "questify-releases", "profile",
            "portfolio-check", "questify", "qbd-demo", "qbd-demo-graphen", "qbd-demo-entwicklung",
            "cv-de", "cv-en"} <= names
    qbd = config.target("qbd-demo")
    assert qbd.min_numbers == 10 and "Status: work in progress" in qbd.required_phrases and "Keine Messwerte" in qbd.forbidden_phrases
    graphs = config.target("qbd-demo-graphen")
    assert graphs.url.endswith("/graphen") and graphs.min_numbers == 60
    assert "Keine Messwerte" in graphs.forbidden_phrases and graphs.forbidden_patterns
    assert config.target("qbd-demo-entwicklung").min_numbers == 100
    assert config.cv_rules["max_pages"] == 2


def test_allowlist_file_extends_the_allowlist(tmp_path, monkeypatch):
    args = _setup(tmp_path)
    private = tmp_path / "private-allowlist.txt"
    private.write_text("# names that stay out of the repo\nMusterstrasse\n")
    seen = {}
    import portfolio_check.cli as cli

    real = cli.Context

    def spy(**kw):
        seen["allowlist"] = list(kw["config"].allowlist)
        return real(**kw)

    monkeypatch.setattr(cli, "Context", spy)
    assert main([*args, "--check", "readme", "--target", "good", "--allowlist-file", str(private)]) == 0
    assert "Musterstrasse" in seen["allowlist"]
    assert main([*args, "--check", "readme", "--allowlist-file", str(tmp_path / "missing.txt")]) == 2
