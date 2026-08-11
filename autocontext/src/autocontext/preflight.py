"""Pre-run preflight checks.

Inspired by Plankton's prereqs.py with 11 static + 4 live checks that
validate the environment before any work begins.
"""
from __future__ import annotations

import logging
import os
from collections.abc import Sequence
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from autocontext.config.settings import load_settings
from autocontext.scenarios import SCENARIO_REGISTRY


@dataclass(frozen=True, slots=True)
class CheckResult:
    """Result of a single preflight check."""

    name: str
    passed: bool
    detail: str
    # AC-914: whether a failure is certain enough to stop the run. Static
    # checks are; a probe that could not reach a conclusion is not.
    blocking: bool = True


class PreflightChecker:
    """Validates the runtime environment before a generation run."""

    def __init__(
        self,
        scenario: str,
        knowledge_root: Path | None = None,
        db_path: Path | None = None,
        settings: Any | None = None,
    ) -> None:
        self._scenario = scenario
        self._knowledge_root = knowledge_root or Path("knowledge")
        self._db_path = db_path
        # AC-914: optional so every existing construction keeps working.
        # Without it the endpoint checks are skipped rather than guessed at.
        self._settings = settings

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

    def check_endpoint(self) -> list[CheckResult]:
        """Probe the LLM endpoint a run would actually use (AC-914).

        Returns [] when no settings were supplied or the transport is not
        HTTP-probeable (CLI runtimes, mlx, anthropic). An absent check is
        honest; a fabricated pass is not.
        """
        if self._settings is None:
            return []
        from autocontext.endpoint_probe import probe_endpoint, resolve_agent_endpoint

        endpoint = resolve_agent_endpoint(self._settings)
        if endpoint is None:
            return []
        return [
            CheckResult(name=probe.name, passed=probe.passed, detail=probe.detail, blocking=probe.certain)
            for probe in probe_endpoint(*endpoint)
        ]

    def run_without_scenario_check(self) -> list[CheckResult]:
        """Everything except the registry lookup.

        Agent-task scenarios are resolved outside SCENARIO_REGISTRY, so that
        check would reject them; the endpoint checks apply just the same.
        """
        return [self.check_knowledge_writable(), *self.check_endpoint()]

    def run_all(self) -> list[CheckResult]:
        """Run all preflight checks."""
        return [
            self.check_scenario_exists(),
            self.check_knowledge_writable(),
            *self.check_endpoint(),
        ]

    @staticmethod
    def blocking_failures(results: Sequence[CheckResult]) -> list[CheckResult]:
        """Failures certain enough to stop a run before it spends tokens.

        A probe that could not determine an answer is not evidence of a
        problem. Treating "unknown" as "broken" would make preflight the thing
        that breaks runs, which is a worse failure than the one it prevents.
        """
        return [r for r in results if not r.passed and r.blocking]

    @staticmethod
    def to_markdown(results: Sequence[CheckResult]) -> str:
        """Format check results as a markdown table."""
        lines = ["## Preflight Checks", ""]
        lines.append("| Check | Status | Detail |")
        lines.append("|-------|--------|--------|")
        for r in results:
            status = "PASS" if r.passed else "FAIL"
            lines.append(f"| {r.name} | {status} | {r.detail} |")
        return "\n".join(lines)


class PreflightBlocked(Exception):
    """Raised when a preflight check is certain the run cannot succeed.

    Carries the failures so the caller can present them; preflight decides,
    the CLI formats. Keeping typer and rich out of this module is what lets
    the server and MCP paths reuse it later.
    """

    def __init__(self, failures: Sequence[CheckResult]) -> None:
        self.failures = list(failures)
        super().__init__("; ".join(f"{f.name}: {f.detail}" for f in self.failures))


def run_preflight(
    scenario: str,
    preset: str | None,
    *,
    check_scenario: bool = True,
) -> list[CheckResult]:
    """Check the environment before a run spends anything (AC-914).

    Returns every result so the caller can report advisories. Raises
    PreflightBlocked only for failures the checks are CERTAIN about -- an
    unreachable endpoint, or a model the server does not serve. Anything that
    could not be determined is returned as a failed-but-non-blocking result: a
    probe that cannot answer is not evidence of a problem, and preflight must
    not become the reason a working setup stops running.

    Until now PreflightChecker existed but was never constructed anywhere, so
    no run has ever been checked. That is the gap this closes; the endpoint
    probes are what make it worth calling.
    """
    # Mirrors cli._apply_preset_env rather than importing it: the cli imports
    # this module, so reaching back would be circular.
    if preset is not None:
        os.environ["AUTOCONTEXT_PRESET"] = preset
    settings = load_settings()
    checker = PreflightChecker(scenario, knowledge_root=Path(settings.knowledge_root), settings=settings)
    try:
        results = checker.run_all() if check_scenario else checker.run_without_scenario_check()
    except Exception:  # noqa: BLE001 - preflight must never be why a run cannot start
        logging.getLogger(__name__).warning("preflight checks errored; continuing", exc_info=True)
        return []

    blocking = PreflightChecker.blocking_failures(results)
    if blocking:
        raise PreflightBlocked(blocking)
    return results
