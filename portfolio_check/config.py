"""Load targets.yaml and cv_rules.yaml into plain dataclasses."""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path

import yaml


@dataclass
class RepoTarget:
    name: str
    slug: str
    ci_workflows: list[str] = field(default_factory=list)
    skip_checks: list[str] = field(default_factory=list)
    skip_reason: str = ""
    kind: str = "repo"

    @property
    def url(self) -> str:
        return f"https://github.com/{self.slug}"


@dataclass
class SiteTarget:
    name: str
    url: str
    required_phrases: list[str] = field(default_factory=list)
    forbidden_phrases: list[str] = field(default_factory=list)
    forbidden_patterns: list[str] = field(default_factory=list)
    required_links: list[str] = field(default_factory=list)
    min_numbers: int = 0
    # URL substrings of images that must be on the page and loaded.
    required_images: list[str] = field(default_factory=list)
    # Seconds the render waits for images that are still loading or retrying.
    image_wait: float = 45.0
    skip_checks: list[str] = field(default_factory=list)
    skip_reason: str = ""
    kind: str = "site"


@dataclass
class CvTarget:
    name: str
    path: str
    language: str
    rules_language: str
    env: str = ""
    skip_checks: list[str] = field(default_factory=list)
    skip_reason: str = ""
    kind: str = "cv"


@dataclass
class Config:
    root: Path
    repos: list[RepoTarget]
    sites: list[SiteTarget]
    cvs: list[CvTarget]
    cv_rules: dict
    allowlist: list[str]
    link_timeout: float = 10.0
    readme_first_screen_lines: int = 60
    link_exceptions: list[dict] = field(default_factory=list)

    @property
    def targets(self) -> list:
        return [*self.repos, *self.sites, *self.cvs]

    def target(self, name: str):
        for t in self.targets:
            if t.name == name:
                return t
        raise KeyError(name)


def read_allowlist(path: Path) -> list[str]:
    """Allowlist lines: one term per line, or "span: TEXT" for an exact passage. # starts a comment."""
    out: list[str] = []
    for line in Path(path).read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if line and not line.startswith("#"):
            out.append(line)
    return out


def _load_yaml(path: Path) -> dict:
    with path.open(encoding="utf-8") as fh:
        return yaml.safe_load(fh) or {}


def load_config(path: str | Path) -> Config:
    path = Path(path).resolve()
    root = path.parent
    raw = _load_yaml(path)
    settings = raw.get("settings", {})

    repos = [RepoTarget(**r) for r in raw.get("repos", [])]
    sites = [SiteTarget(**s) for s in raw.get("sites", [])]
    cvs = [CvTarget(**c) for c in raw.get("cvs", [])]

    names = [t.name for t in (*repos, *sites, *cvs)]
    dupes = {n for n in names if names.count(n) > 1}
    if dupes:
        raise ValueError(f"duplicate target names in {path.name}: {sorted(dupes)}")

    rules_file = settings.get("cv_rules", "cv_rules.yaml")
    cv_rules = _load_yaml(root / rules_file) if (root / rules_file).exists() else {}

    allow_file = settings.get("languagetool_allowlist", "allowlist.txt")
    allowlist = read_allowlist(root / allow_file) if (root / allow_file).exists() else []

    return Config(
        root=root,
        repos=repos,
        sites=sites,
        cvs=cvs,
        cv_rules=cv_rules,
        allowlist=allowlist,
        link_timeout=float(settings.get("link_timeout", 10)),
        readme_first_screen_lines=int(settings.get("readme_first_screen_lines", 60)),
        link_exceptions=[_link_exception(e) for e in settings.get("link_exceptions", []) or []],
    )


def _link_exception(raw: dict) -> dict:
    """One entry of settings.link_exceptions: host, status list and a required reason."""
    host = str(raw.get("host", "")).lower().strip()
    statuses = [int(s) for s in raw.get("status", [])]
    reason = str(raw.get("reason", "")).strip()
    if not host or not statuses or not reason:
        raise ValueError(f"link_exceptions entry needs host, status and reason: {raw}")
    return {"host": host, "status": statuses, "reason": reason}
