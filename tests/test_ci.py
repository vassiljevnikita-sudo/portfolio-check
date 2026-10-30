from portfolio_check.checks import ci
from portfolio_check.result import FAIL, PASS

from .conftest import FakeResponse


def _api(runs, repo_status=200, private=False):
    def github_api(path, params=None):
        if path.endswith("/actions/runs"):
            assert params["branch"] == "main" and params["status"] == "completed"
            return FakeResponse(json_data={"workflow_runs": runs})
        return FakeResponse(status=repo_status, json_data={"default_branch": "main", "private": private})

    return github_api


def _run(name, conclusion, n=1):
    return {"path": f".github/workflows/{name}", "name": name, "conclusion": conclusion,
            "html_url": f"https://github.com/r/{n}", "created_at": "2026-10-30T05:00:00Z"}


def test_ci_passes_when_latest_run_per_workflow_succeeded(ctx):
    ctx.github_api = _api([_run("test.yml", "success", 2), _run("test.yml", "failure", 1)])
    assert ci.run(ctx, ctx.config.repos[0]).status == PASS


def test_ci_fails_when_latest_run_failed(ctx):
    ctx.github_api = _api([_run("test.yml", "failure", 2), _run("test.yml", "success", 1)])
    result = ci.run(ctx, ctx.config.repos[0])
    assert result.status == FAIL and "test.yml: failure" in result.evidence[0]


def test_ci_fails_without_runs_or_repo(ctx):
    ctx.github_api = _api([])
    assert "no completed workflow runs" in ci.run(ctx, ctx.config.repos[0]).summary
    ctx.github_api = _api([], repo_status=404)
    assert "not found" in ci.run(ctx, ctx.config.repos[0]).summary


def test_ci_workflows_filter_ignores_other_workflows(ctx):
    repo = ctx.config.repos[0]
    repo.ci_workflows = ["test.yml"]
    ctx.github_api = _api([_run("portfolio.yml", "failure", 3), _run("test.yml", "success", 2)])
    assert ci.run(ctx, repo).status == PASS
