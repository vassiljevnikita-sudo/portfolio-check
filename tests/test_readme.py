import shutil

from portfolio_check.checks import readme
from portfolio_check.result import FAIL, PASS

from .conftest import FIXTURES, ROOT


def _repo_with(ctx, tmp_path, fixture):
    repo_dir = tmp_path / "repo"
    repo_dir.mkdir()
    shutil.copy(FIXTURES / fixture, repo_dir / "README.md")
    ctx.local_paths["demo-repo"] = repo_dir
    return ctx.config.repos[0]


def test_readme_contract_passes(ctx, tmp_path):
    result = readme.run(ctx, _repo_with(ctx, tmp_path, "README_good.md"))
    assert result.status == PASS, result.evidence
    assert any("problem statement" in e for e in result.evidence)


def test_readme_contract_fails_and_names_missing_items(ctx, tmp_path):
    result = readme.run(ctx, _repo_with(ctx, tmp_path, "README_bad.md"))
    assert result.status == FAIL
    missing = result.data["missing"]
    assert any("screenshot" in m for m in missing)  # the CI badge does not count
    assert any("Try it" in m for m in missing)
    assert any("My role" in m for m in missing)
    assert any("limitations" in m for m in missing)


def test_items_below_first_screen_do_not_count():
    text = (FIXTURES / "README_good.md").read_text()
    lines = text.splitlines()
    pushed = "\n".join(lines[:3] + ["filler"] * 80 + lines[3:])
    missing, _ = readme.evaluate(pushed, first_screen=60)
    assert missing


def test_try_it_heading_without_link_or_command_does_not_count():
    text = "# T\n\nA tool that does one thing well enough.\n\n![s](a.png)\n\n## Try it\n\nSoon.\n\n## My role\nx\n## Tech stack\nx\n## Limitations\nx\n"
    missing, _ = readme.evaluate(text)
    assert missing == ['"Try it" link or demo command']


def test_own_readme_meets_the_contract():
    missing, evidence = readme.evaluate((ROOT / "README.md").read_text(encoding="utf-8"))
    assert missing == [], evidence
