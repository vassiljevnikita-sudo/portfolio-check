"""Shared run state: HTTP session, repo clones, rendered pages, denylist, CV paths.

Everything that touches the network or external tools goes through this object,
so unit tests can replace single methods with fakes.
"""

from __future__ import annotations

import os
import shutil
import subprocess
from dataclasses import dataclass, field
from pathlib import Path

import requests

from .config import Config, CvTarget, RepoTarget, SiteTarget

USER_AGENT = (
    "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 (KHTML, like Gecko) "
    "Chrome/130.0 Safari/537.36 portfolio-check/0.1"
)


class TargetUnavailable(Exception):
    """The target cannot be reached the way a logged-out visitor would reach it."""


@dataclass
class RenderResult:
    url: str
    final_url: str
    text: str
    html: str
    links: list[str]
    screenshot: str | None = None
    error: str | None = None


@dataclass
class Context:
    config: Config
    out_dir: Path
    cache_dir: Path
    denylist: list[str] = field(default_factory=list)
    local_paths: dict[str, Path] = field(default_factory=dict)
    require_cv: bool = False
    github_token: str | None = None
    languagetool_url: str = "https://api.languagetool.org/v2/check"
    gitleaks_bin: str = "gitleaks"
    soffice_bin: str = "soffice"
    http: requests.Session = field(default_factory=requests.Session)
    _clones: dict = field(default_factory=dict)
    _renders: dict = field(default_factory=dict)
    _browser: object = None
    _playwright: object = None

    def __post_init__(self):
        self.http.headers.setdefault("User-Agent", USER_AGENT)
        self.http.headers["User-Agent"] = USER_AGENT

    # ---------------------------------------------------------------- HTTP

    def fetch(self, url: str, timeout: float | None = None) -> requests.Response:
        """GET without any credentials. Body is streamed and not downloaded."""
        resp = self.http.get(
            url,
            timeout=timeout or self.config.link_timeout,
            allow_redirects=True,
            stream=True,
        )
        resp.close()
        return resp

    def get_text(self, url: str) -> tuple[int, str]:
        resp = self.http.get(url, timeout=self.config.link_timeout * 2, allow_redirects=True)
        return resp.status_code, resp.text

    def github_api(self, path: str, params: dict | None = None) -> requests.Response:
        headers = {"Accept": "application/vnd.github+json", "X-GitHub-Api-Version": "2022-11-28"}
        if self.github_token:
            headers["Authorization"] = f"Bearer {self.github_token}"
        return requests.get(
            f"https://api.github.com/{path.lstrip('/')}",
            headers=headers,
            params=params,
            timeout=30,
        )

    # ---------------------------------------------------------------- repos

    def repo_dir(self, repo: RepoTarget) -> Path:
        """Local checkout of a repo: the --local override, or an anonymous full clone."""
        if repo.name in self.local_paths:
            path = self.local_paths[repo.name]
            if not path.exists():
                raise TargetUnavailable(f"local path {path} does not exist")
            return path
        if repo.name in self._clones:
            cached = self._clones[repo.name]
            if isinstance(cached, Exception):
                raise cached
            return cached
        dest = self.cache_dir / "repos" / repo.name
        try:
            self._clone(repo, dest)
        except TargetUnavailable as exc:
            self._clones[repo.name] = exc
            raise
        self._clones[repo.name] = dest
        return dest

    def _clone(self, repo: RepoTarget, dest: Path) -> None:
        env = {**os.environ, "GIT_TERMINAL_PROMPT": "0", "GIT_ASKPASS": "/bin/false"}
        # Empty credential helper: clone the way a logged-out visitor can.
        git = ["git", "-c", "credential.helper=", "-c", "core.askPass=/bin/false"]
        if dest.exists():
            shutil.rmtree(dest)
        dest.parent.mkdir(parents=True, exist_ok=True)
        proc = subprocess.run(
            [*git, "clone", "--quiet", f"https://github.com/{repo.slug}.git", str(dest)],
            capture_output=True,
            text=True,
            env=env,
            timeout=600,
        )
        if proc.returncode != 0:
            msg = (proc.stderr or proc.stdout).strip().splitlines()
            raise TargetUnavailable(
                f"anonymous clone of {repo.slug} failed: {msg[-1] if msg else 'unknown error'}"
            )

    def is_local(self, target) -> bool:
        return target.name in self.local_paths

    # ---------------------------------------------------------------- sites

    def render(self, site: SiteTarget) -> RenderResult:
        if site.name not in self._renders:
            self._renders[site.name] = self._render(site)
        return self._renders[site.name]

    def _render(self, site: SiteTarget) -> RenderResult:
        try:
            if self._browser is None:
                from playwright.sync_api import sync_playwright

                self._playwright = sync_playwright().start()
                self._browser = self._playwright.chromium.launch()
            # Fresh context per site: no cookies, no storage, like a first visit.
            context = self._browser.new_context(viewport={"width": 1280, "height": 900})
            page = context.new_page()
            page.goto(site.url, wait_until="networkidle", timeout=60_000)
            shots = self.out_dir / "screenshots"
            shots.mkdir(parents=True, exist_ok=True)
            shot = shots / f"{site.name}.png"
            page.screenshot(path=str(shot), full_page=True)
            result = RenderResult(
                url=site.url,
                final_url=page.url,
                text=page.inner_text("body"),
                html=page.content(),
                links=page.eval_on_selector_all("a[href]", "els => els.map(e => e.href)"),
                screenshot=str(shot.relative_to(self.out_dir)),
            )
            context.close()
            return result
        except Exception as exc:  # noqa: BLE001 - reported as evidence
            return RenderResult(site.url, site.url, "", "", [], None, f"{type(exc).__name__}: {exc}")

    def close(self) -> None:
        if self._browser is not None:
            self._browser.close()
            self._playwright.stop()
            self._browser = None

    # ---------------------------------------------------------------- CVs

    def cv_path(self, cv: CvTarget) -> tuple[Path | None, bool]:
        """Return (path, explicit). explicit=True when a flag or env var named it."""
        if cv.name in self.local_paths:
            return self.local_paths[cv.name], True
        if cv.env and os.environ.get(cv.env):
            return Path(os.environ[cv.env]), True
        default = self.config.root / cv.path
        return default, False
