"""Check registry: name -> (target kinds it applies to, function)."""

from __future__ import annotations

from typing import Callable

from . import ci, cv_content, cv_form, denylist, links, readme, render, secrets

CHECKS: dict[str, tuple[tuple[str, ...], Callable]] = {
    "links": (("repo", "site", "cv"), links.run),
    "render": (("site",), render.run),
    "readme": (("repo",), readme.run),
    "secrets": (("repo",), secrets.run),
    "denylist": (("repo", "site"), denylist.run),
    "ci": (("repo",), ci.run),
    "cv-content": (("cv",), cv_content.run),
    "cv-form": (("cv",), cv_form.run),
}

ALIASES = {
    "demo": "render",
    "demo-render": "render",
    "readme-contract": "readme",
    "secret": "secrets",
    "gitleaks": "secrets",
    "employer-denylist": "denylist",
    "ci-status": "ci",
}


def canonical(name: str) -> str:
    key = name.strip().lower().replace("_", "-")
    key = ALIASES.get(key, key)
    if key not in CHECKS:
        raise KeyError(name)
    return key
