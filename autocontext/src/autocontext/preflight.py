"""Pre-run preflight checks.

Inspired by Plankton's prereqs.py with 11 static + 4 live checks that
validate the environment before any work begins.
"""
from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

from autocontext.scenarios import SCENARIO_REGISTRY


@dataclass(frozen=True, slots=True)
class CheckResult:
    """Result of a single preflight check."""

    name: str
    passed: bool
    detail: str


class PreflightChecker:
    """Validates the runtime environment before a generation run."""

    def __init__(
        self,
        scenario: str,
        knowledge_root: Path | None = None,
        db_path: Path | None = None,
    ) -> None:
        self._scenario = scenario
        self._knowledge_root = knowledge_root or Path("knowledge")
        self._db_path = db_path

    def check_scenario_exists(self) -> CheckResult:
        """Check if the scenario is registered."""
        exists = self._scenario in SCENARIO_REGISTRY
        return CheckResult(
            name="scenario_exists",
            passed=exists,
            detail=f"Scenario '{self._scenario}' {'found' if exists else 'not found'} in registry",
        )

    def check_knowledge_writable(self) -> CheckResult:
        """Check if the knowledge directory is writable."""
        test_file = None
        try:
            self._knowledge_root.mkdir(parents=True, exist_ok=True)
            test_file = self._knowledge_root / ".preflight_test"
            test_file.write_text("test")
            return CheckResult(name="knowledge_writable", passed=True, detail="Knowledge dir writable")
        except OSError as e:
            return CheckResult(name="knowledge_writable", passed=False, detail=str(e))
        finally:
            if test_file is not None:
                test_file.unlink(missing_ok=True)

    def run_all(self) -> list[CheckResult]:
        """Run all preflight checks."""
        return [
            self.check_scenario_exists(),
            self.check_knowledge_writable(),
        ]

    @staticmethod
    def to_markdown(results: list[CheckResult]) -> str:
        """Format check results as a markdown table."""
        lines = ["## Preflight Checks", ""]
        lines.append("| Check | Status | Detail |")
        lines.append("|-------|--------|--------|")
        for r in results:
            status = "PASS" if r.passed else "FAIL"
            lines.append(f"| {r.name} | {status} | {r.detail} |")
        return "\n".join(lines)
