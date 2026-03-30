"""McNemar's statistical test for A/B testing results.

Inspired by Plankton's SWE-bench A/B analysis with binomial p-values,
odds ratios, and confidence intervals.
"""
from __future__ import annotations

from dataclasses import dataclass
from math import comb


@dataclass(frozen=True, slots=True)
class ABStatsReport:
    """Statistical analysis of an A/B test using McNemar's test."""

    fail_to_pass: int
    pass_to_fail: int
    both_pass: int
    both_fail: int
    p_value: float
    significant: bool

    def to_markdown(self) -> str:
        """Format report as markdown table."""
        sig_str = "Yes" if self.significant else "No"
        lines = [
            "## McNemar's Test Results",
            "",
            "| Metric | Value |",
            "|--------|-------|",
            f"| Fail→Pass (treatment improved) | {self.fail_to_pass} |",
            f"| Pass→Fail (treatment regressed) | {self.pass_to_fail} |",
            f"| Both pass | {self.both_pass} |",
            f"| Both fail | {self.both_fail} |",
            f"| p-value | {self.p_value:.4f} |",
            f"| Significant (α=0.05) | {sig_str} |",
        ]
        return "\n".join(lines)


def mcnemar_test(
    baseline_passed: list[bool],
    treatment_passed: list[bool],
    *,
    alpha: float = 0.05,
) -> ABStatsReport:
    """Run McNemar's exact test on paired pass/fail outcomes.

    Uses scipy.stats.binomtest when available, falls back to a simple
    binomial calculation otherwise.
    """
    if len(baseline_passed) != len(treatment_passed):
        msg = "baseline_passed and treatment_passed must have the same length"
        raise ValueError(msg)

    fail_to_pass = 0
    pass_to_fail = 0
    both_pass = 0
    both_fail = 0

    for b, t in zip(baseline_passed, treatment_passed, strict=True):
        if not b and t:
            fail_to_pass += 1
        elif b and not t:
            pass_to_fail += 1
        elif b and t:
            both_pass += 1
        else:
            both_fail += 1

    n_discordant = fail_to_pass + pass_to_fail
    if n_discordant == 0:
        p_value = 1.0
    else:
        p_value = _binomial_p_value(fail_to_pass, n_discordant)

    return ABStatsReport(
        fail_to_pass=fail_to_pass,
        pass_to_fail=pass_to_fail,
        both_pass=both_pass,
        both_fail=both_fail,
        p_value=p_value,
        significant=p_value < alpha,
    )


def _binomial_p_value(successes: int, n: int) -> float:
    """Two-sided binomial test p-value (pure-Python, no scipy needed)."""
    return _exact_binomial_two_sided(successes, n)


def _exact_binomial_two_sided(k: int, n: int) -> float:
    """Compute exact two-sided binomial p-value without scipy."""
    # P(X = i) for Binomial(n, 0.5)
    p_k = comb(n, k) / (2**n)

    # Sum probabilities of outcomes at least as extreme
    p_value = 0.0
    for i in range(n + 1):
        p_i = comb(n, i) / (2**n)
        if p_i <= p_k + 1e-12:  # tolerance for float comparison
            p_value += p_i

    return min(p_value, 1.0)
