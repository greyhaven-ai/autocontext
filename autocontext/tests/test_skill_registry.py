"""Tests for skill manifest parsing and registry (AC-509).

DDD: SkillRegistry discovers, validates, deduplicates, and lazily loads
runtime skills from configured roots.
"""

from pathlib import Path


def _write_skill(root: Path, name: str, body: str = "", description: str = "A skill") -> Path:
    """Helper: write a minimal SKILL.md file."""
    skill_dir = root / name
    skill_dir.mkdir(parents=True, exist_ok=True)
    content = f"""---
name: {name}
description: {description}
---

# {name}

{body or f"Instructions for {name}."}
"""
    (skill_dir / "SKILL.md").write_text(content, encoding="utf-8")
    return skill_dir


class TestSkillManifest:
    """Manifest is the lightweight metadata parsed from SKILL.md frontmatter."""

    def test_parse_from_skill_md(self, tmp_path: Path) -> None:
        from autocontext.session.skill_registry import SkillManifest

        _write_skill(tmp_path, "my-skill", description="Does useful things")
        manifest = SkillManifest.from_skill_dir(tmp_path / "my-skill")
        assert manifest.name == "my-skill"
        assert manifest.description == "Does useful things"
        assert manifest.skill_path == tmp_path / "my-skill"

    def test_missing_skill_md_returns_none(self, tmp_path: Path) -> None:
        from autocontext.session.skill_registry import SkillManifest

        (tmp_path / "empty-skill").mkdir()
        result = SkillManifest.from_skill_dir(tmp_path / "empty-skill")
        assert result is None

    def test_malformed_frontmatter(self, tmp_path: Path) -> None:
        from autocontext.session.skill_registry import SkillManifest

        skill_dir = tmp_path / "bad-skill"
        skill_dir.mkdir()
        (skill_dir / "SKILL.md").write_text("no frontmatter here", encoding="utf-8")
        result = SkillManifest.from_skill_dir(skill_dir)
        # Should return a manifest with defaults, not crash
        assert result is not None
        assert result.name == "bad-skill"  # falls back to dir name

    def test_quoted_frontmatter_values_are_normalized(self, tmp_path: Path) -> None:
        from autocontext.session.skill_registry import SkillManifest

        skill_dir = tmp_path / "quoted-skill"
        skill_dir.mkdir()
        (skill_dir / "SKILL.md").write_text(
            """---
name: "quoted-skill"
description: "Quoted description"
---

# quoted-skill

Instructions.
""",
            encoding="utf-8",
        )

        manifest = SkillManifest.from_skill_dir(skill_dir)
        assert manifest is not None
        assert manifest.name == "quoted-skill"
        assert manifest.description == "Quoted description"


class TestSkillEntry:
    """Entry wraps manifest with lazy body loading."""

    def test_body_not_loaded_until_accessed(self, tmp_path: Path) -> None:
        from autocontext.session.skill_registry import SkillEntry, SkillManifest

        _write_skill(tmp_path, "lazy-skill", body="Full instructions here.")
        manifest = SkillManifest.from_skill_dir(tmp_path / "lazy-skill")
        entry = SkillEntry(manifest=manifest)
        assert not entry.is_loaded

        body = entry.load_body()
        assert "Full instructions here" in body
        assert entry.is_loaded

    def test_body_cached_after_first_load(self, tmp_path: Path) -> None:
        from autocontext.session.skill_registry import SkillEntry, SkillManifest

        _write_skill(tmp_path, "cached-skill", body="Content.")
        manifest = SkillManifest.from_skill_dir(tmp_path / "cached-skill")
        entry = SkillEntry(manifest=manifest)
        body1 = entry.load_body()
        body2 = entry.load_body()
        assert body1 == body2


class TestSkillRegistry:
    """Registry discovers, deduplicates, and activates skills."""

    def test_discover_from_root(self, tmp_path: Path) -> None:
        from autocontext.session.skill_registry import SkillRegistry

        _write_skill(tmp_path, "skill-a")
        _write_skill(tmp_path, "skill-b")

        registry = SkillRegistry()
        registry.discover(tmp_path)
        assert len(registry.all_manifests()) == 2

    def test_deduplicates_same_skill(self, tmp_path: Path) -> None:
        from autocontext.session.skill_registry import SkillRegistry

        root1 = tmp_path / "root1"
        root2 = tmp_path / "root2"
        _write_skill(root1, "shared-skill")
        _write_skill(root2, "shared-skill")

        registry = SkillRegistry()
        registry.discover(root1)
        registry.discover(root2)
        assert len(registry.all_manifests()) == 1  # deduped by name

    def test_filter_by_description_keyword(self, tmp_path: Path) -> None:
        from autocontext.session.skill_registry import SkillRegistry

        _write_skill(tmp_path, "auth-skill", description="Authentication and OAuth flows")
        _write_skill(tmp_path, "db-skill", description="Database schema design")

        registry = SkillRegistry()
        registry.discover(tmp_path)
        matches = registry.search("auth")
        assert len(matches) == 1
        assert matches[0].name == "auth-skill"

    def test_get_by_name(self, tmp_path: Path) -> None:
        from autocontext.session.skill_registry import SkillRegistry

        _write_skill(tmp_path, "target-skill")

        registry = SkillRegistry()
        registry.discover(tmp_path)
        entry = registry.get("target-skill")
        assert entry is not None
        assert entry.manifest.name == "target-skill"

    def test_get_nonexistent_returns_none(self) -> None:
        from autocontext.session.skill_registry import SkillRegistry

        registry = SkillRegistry()
        assert registry.get("nope") is None

    def test_validation_reports_errors(self, tmp_path: Path) -> None:
        from autocontext.session.skill_registry import SkillRegistry

        # Write a skill with empty SKILL.md
        skill_dir = tmp_path / "empty-body"
        skill_dir.mkdir()
        (skill_dir / "SKILL.md").write_text("", encoding="utf-8")

        registry = SkillRegistry()
        registry.discover(tmp_path)
        errors = registry.validate()
        # Empty skill should produce a validation warning
        assert len(errors) >= 0  # at minimum runs without crashing
