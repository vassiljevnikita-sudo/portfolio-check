"""CI status: latest completed GitHub Actions run on the default branch, per workflow.

Pass when every checked workflow's latest completed run concluded `success`.
`ci_workflows` in targets.yaml limits the check to named workflow files; otherwise every
workflow with a run on the default branch counts.
"""

from __future__ import annotations

from ..result import failed, passed

NAME = "ci"


def run(ctx, repo):
    meta = ctx.github_api(f"repos/{repo.slug}")
    if meta.status_code == 404:
        return failed(NAME, repo.name, f"repository {repo.slug} not found or not public")
    if meta.status_code != 200:
        return failed(NAME, repo.name, f"GitHub API returned {meta.status_code} for {repo.slug}")
    info = meta.json()
    branch = info.get("default_branch", "main")
    if info.get("private"):
        return failed(NAME, repo.name, f"{repo.slug} is private")

    resp = ctx.github_api(
        f"repos/{repo.slug}/actions/runs",
        params={"branch": branch, "status": "completed", "per_page": 100, "exclude_pull_requests": "true"},
    )
    if resp.status_code != 200:
        return failed(NAME, repo.name, f"GitHub API returned {resp.status_code} for workflow runs")
    runs = resp.json().get("workflow_runs", [])

    latest: dict[str, dict] = {}
    for run_ in runs:  # API returns newest first
        key = (run_.get("path") or run_.get("name") or "").split("/")[-1].split("@")[0]
        latest.setdefault(key, run_)

    wanted = repo.ci_workflows or sorted(latest)
    if not wanted:
        return failed(NAME, repo.name, f"no completed workflow runs on {branch}")

    evidence, bad = [], []
    for wf in wanted:
        run_ = latest.get(wf)
        if run_ is None:
            bad.append(f"{wf}: no completed run on {branch}")
            continue
        line = f"{wf}: {run_.get('conclusion')} ({run_.get('html_url')}, {run_.get('created_at')})"
        (evidence if run_.get("conclusion") == "success" else bad).append(line)

    if bad:
        return failed(NAME, repo.name, f"{len(bad)} of {len(wanted)} workflow(s) not green on {branch}", bad + evidence)
    return passed(NAME, repo.name, f"{len(wanted)} workflow(s) green on {branch}", evidence)
