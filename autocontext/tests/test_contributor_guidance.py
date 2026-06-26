from __future__ import annotations

from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[2]


def _read_repo_file(path: str) -> str:
    return (REPO_ROOT / path).read_text(encoding="utf-8")


def test_parity_last_guidance_is_documented_for_people_and_agents() -> None:
    for path in ("CONTRIBUTING.md", "AGENTS.md"):
        text = _read_repo_file(path)
        assert "parity-last" in text.lower()
        assert "one runtime first" in text.lower()
        assert "user-visible" in text.lower()


def test_pr_template_requires_parity_decision() -> None:
    text = _read_repo_file(".github/PULL_REQUEST_TEMPLATE.md")

    assert "Parity decision" in text
    assert "required now" in text
    assert "deferred" in text
