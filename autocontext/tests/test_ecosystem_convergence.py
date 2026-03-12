"""Tests for ecosystem convergence detection (MTS-28)."""
from __future__ import annotations

from autocontext.loop.ecosystem_runner import compute_playbook_divergence, detect_oscillation


def test_divergence_identical() -> None:
    """Identical playbooks have 0.0 divergence."""
    assert compute_playbook_divergence("# Strategy\nBe aggressive.", "# Strategy\nBe aggressive.") == 0.0


def test_divergence_completely_different() -> None:
    """Completely different playbooks have high divergence."""
    d = compute_playbook_divergence("alpha beta gamma", "xray yankee zulu")
    assert d > 0.5


def test_divergence_empty_strings() -> None:
    """Two empty strings have 0.0 divergence."""
    assert compute_playbook_divergence("", "") == 0.0


def test_divergence_one_empty() -> None:
    """One empty playbook has 1.0 divergence."""
    assert compute_playbook_divergence("some content", "") == 1.0


def test_oscillation_detected() -> None:
    """Oscillation detected when divergence exceeds threshold for N cycles."""
    history = [0.6, 0.7, 0.65, 0.8]  # All above 0.5 threshold
    assert detect_oscillation(history, threshold=0.5, window=3) is True


def test_oscillation_not_detected_below_threshold() -> None:
    """No oscillation when divergence is below threshold."""
    history = [0.1, 0.2, 0.15, 0.05]
    assert detect_oscillation(history, threshold=0.5, window=3) is False


def test_oscillation_not_detected_insufficient_history() -> None:
    """No oscillation with insufficient history."""
    history = [0.8, 0.9]  # Only 2 entries, window=3
    assert detect_oscillation(history, threshold=0.5, window=3) is False


def test_oscillation_empty_history() -> None:
    """Empty history → no oscillation."""
    assert detect_oscillation([], threshold=0.5, window=3) is False
