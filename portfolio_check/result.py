from __future__ import annotations

from dataclasses import asdict, dataclass, field

PASS = "pass"
FAIL = "fail"
SKIP = "skip"


@dataclass
class CheckResult:
    check: str
    target: str
    status: str
    summary: str
    evidence: list[str] = field(default_factory=list)
    data: dict = field(default_factory=dict)

    @property
    def failed(self) -> bool:
        return self.status == FAIL

    def to_dict(self) -> dict:
        return asdict(self)


def passed(check: str, target: str, summary: str, evidence=None, **data) -> CheckResult:
    return CheckResult(check, target, PASS, summary, list(evidence or []), data)


def failed(check: str, target: str, summary: str, evidence=None, **data) -> CheckResult:
    return CheckResult(check, target, FAIL, summary, list(evidence or []), data)


def skipped(check: str, target: str, summary: str, evidence=None, **data) -> CheckResult:
    return CheckResult(check, target, SKIP, summary, list(evidence or []), data)
