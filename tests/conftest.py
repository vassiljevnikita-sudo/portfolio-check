"""Shared fixtures. No test touches the network or needs gitleaks or LibreOffice.

Only test_render_browser.py uses Chromium (with a local server); it skips itself without it.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from portfolio_check.config import Config, CvTarget, RepoTarget, SiteTarget, load_config
from portfolio_check.context import Context

FIXTURES = Path(__file__).parent / "fixtures"
ROOT = Path(__file__).resolve().parent.parent


class FakeResponse:
    def __init__(self, status=200, url="", json_data=None, text="", headers=None):
        self.status_code = status
        self.url = url
        self._json = json_data
        self.text = text
        self.headers = headers or {}

    def json(self):
        return self._json

    def close(self):
        pass


@pytest.fixture
def config(tmp_path) -> Config:
    real = load_config(ROOT / "targets.yaml")
    return Config(
        root=tmp_path,
        repos=[RepoTarget(name="demo-repo", slug="someone/demo-repo")],
        sites=[
            SiteTarget(
                name="demo-site",
                url="https://demo.example/",
                required_phrases=["Built by Test Person"],
                forbidden_phrases=["Keine Messwerte"],
                forbidden_patterns=["^\\s*[—–-]\\s*$"],
                required_links=["https://github.com/someone"],
                min_numbers=4,
            )
        ],
        cvs=[
            CvTarget(name="cv-en", path="cv/missing.docx", language="en-US", rules_language="en", env="TEST_CV_EN")
        ],
        cv_rules=real.cv_rules,
        allowlist=real.allowlist,
    )


@pytest.fixture
def ctx(config, tmp_path) -> Context:
    return Context(config=config, out_dir=tmp_path / "out", cache_dir=tmp_path / "cache")
