"""Tests for active_library_books field on SkillPackage."""
from __future__ import annotations

from autocontext.knowledge.export import SkillPackage


def test_skill_package_library_fields() -> None:
    pkg = SkillPackage(
        scenario_name="grid_ctf",
        display_name="Grid CTF",
        description="Capture the flag",
        playbook="playbook content",
        lessons=["lesson 1"],
        best_strategy=None,
        best_score=0.0,
        best_elo=1000,
        hints="hints",
        harness={},
        metadata={},
        active_library_books=["clean-arch", "ddd"],
    )
    d = pkg.to_dict()
    assert d["active_library_books"] == ["clean-arch", "ddd"]


def test_skill_package_library_fields_default() -> None:
    pkg = SkillPackage(
        scenario_name="grid_ctf",
        display_name="Grid CTF",
        description="test",
        playbook="",
        lessons=[],
        best_strategy=None,
        best_score=0.0,
        best_elo=1000,
        hints="",
        harness={},
        metadata={},
    )
    assert pkg.active_library_books is None
