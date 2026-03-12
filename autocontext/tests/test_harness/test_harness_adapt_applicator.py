"""Tests for ConfigApplicator — safety guardrails and config application."""

from __future__ import annotations

from pathlib import Path

from autocontext.config.settings import AppSettings
from autocontext.harness.adapt.applicator import ConfigApplicator
from autocontext.harness.adapt.types import AdaptationPolicy, AdaptationStatus
from autocontext.harness.audit.types import AuditCategory
from autocontext.harness.audit.writer import AppendOnlyAuditWriter
from autocontext.harness.meta.types import ConfigRecommendation


def _make_rec(
    role: str = "analyst",
    parameter: str = "model",
    current_value: str = "claude-sonnet-4-5-20250929",
    recommended_value: str = "claude-haiku-4-5-20251001",
    confidence: float = 0.8,
    rationale: str = "cheaper with similar quality",
) -> ConfigRecommendation:
    return ConfigRecommendation(
        role=role,
        parameter=parameter,
        current_value=current_value,
        recommended_value=recommended_value,
        confidence=confidence,
        rationale=rationale,
    )


def _enabled_policy(**overrides: object) -> AdaptationPolicy:
    defaults: dict[str, object] = {
        "enabled": True,
        "min_confidence": 0.6,
        "max_changes_per_cycle": 2,
        "dry_run": False,
    }
    defaults.update(overrides)
    return AdaptationPolicy(**defaults)  # type: ignore[arg-type]


class TestDisabledPolicy:
    def test_disabled_policy_skips_all(self) -> None:
        settings = AppSettings()
        policy = AdaptationPolicy(enabled=False)
        rec = _make_rec()
        applicator = ConfigApplicator()

        new_settings, results = applicator.apply(settings, [rec], policy)

        assert len(results) == 1
        assert results[0].status == AdaptationStatus.SKIPPED_DISABLED
        # Settings unchanged
        assert new_settings.model_analyst == settings.model_analyst


class TestModelApplication:
    def test_applies_model_recommendation(self) -> None:
        settings = AppSettings()
        policy = _enabled_policy()
        rec = _make_rec(role="analyst", recommended_value="claude-haiku-4-5-20251001")
        applicator = ConfigApplicator()

        new_settings, results = applicator.apply(settings, [rec], policy)

        assert len(results) == 1
        assert results[0].status == AdaptationStatus.APPLIED
        assert new_settings.model_analyst == "claude-haiku-4-5-20251001"

    def test_model_field_mapping_per_role(self) -> None:
        settings = AppSettings()
        policy = _enabled_policy(max_changes_per_cycle=10)
        roles_and_fields = {
            "competitor": "model_competitor",
            "analyst": "model_analyst",
            "coach": "model_coach",
            "architect": "model_architect",
            "curator": "model_curator",
            "translator": "model_translator",
        }

        for role, field_name in roles_and_fields.items():
            rec = _make_rec(role=role, recommended_value="new-model-x")
            applicator = ConfigApplicator()
            new_settings, results = applicator.apply(settings, [rec], policy)
            assert getattr(new_settings, field_name) == "new-model-x", f"Failed for role={role}"
            assert results[0].status == AdaptationStatus.APPLIED

    def test_multiple_roles_applied(self) -> None:
        settings = AppSettings()
        policy = _enabled_policy(max_changes_per_cycle=5)
        recs = [
            _make_rec(role="analyst", recommended_value="model-a"),
            _make_rec(role="coach", recommended_value="model-b"),
        ]
        applicator = ConfigApplicator()

        new_settings, results = applicator.apply(settings, recs, policy)

        assert len(results) == 2
        assert all(r.status == AdaptationStatus.APPLIED for r in results)
        assert new_settings.model_analyst == "model-a"
        assert new_settings.model_coach == "model-b"


class TestCadenceApplication:
    def test_applies_cadence_recommendation(self) -> None:
        settings = AppSettings()
        policy = _enabled_policy()
        rec = _make_rec(
            role="architect",
            parameter="cadence",
            current_value="every 3 generations",
            recommended_value="every 2-3 generations",
            confidence=0.9,
        )
        applicator = ConfigApplicator()

        new_settings, results = applicator.apply(settings, [rec], policy)

        assert len(results) == 1
        assert results[0].status == AdaptationStatus.APPLIED
        assert new_settings.architect_every_n_gens == 3  # upper bound of "2-3"

    def test_cadence_parsing_extracts_upper_bound(self) -> None:
        settings = AppSettings()
        policy = _enabled_policy()
        rec = _make_rec(
            role="architect",
            parameter="cadence",
            current_value="every 3 generations",
            recommended_value="every 5 generations",
            confidence=0.9,
        )
        applicator = ConfigApplicator()

        new_settings, results = applicator.apply(settings, [rec], policy)

        assert results[0].status == AdaptationStatus.APPLIED
        assert new_settings.architect_every_n_gens == 5


class TestPolicyGuardrails:
    def test_skips_low_confidence(self) -> None:
        settings = AppSettings()
        policy = _enabled_policy(min_confidence=0.7)
        rec = _make_rec(confidence=0.5)
        applicator = ConfigApplicator()

        new_settings, results = applicator.apply(settings, [rec], policy)

        assert len(results) == 1
        assert results[0].status == AdaptationStatus.SKIPPED_LOW_CONFIDENCE
        assert new_settings.model_analyst == settings.model_analyst

    def test_caps_max_changes(self) -> None:
        settings = AppSettings()
        policy = _enabled_policy(max_changes_per_cycle=1)
        recs = [
            _make_rec(role="analyst", recommended_value="model-a"),
            _make_rec(role="coach", recommended_value="model-b"),
        ]
        applicator = ConfigApplicator()

        new_settings, results = applicator.apply(settings, recs, policy)

        assert len(results) == 2
        assert results[0].status == AdaptationStatus.APPLIED
        assert results[1].status == AdaptationStatus.SKIPPED_MAX_CHANGES
        assert new_settings.model_analyst == "model-a"
        assert new_settings.model_coach == settings.model_coach

    def test_dry_run_does_not_mutate(self) -> None:
        settings = AppSettings()
        policy = _enabled_policy(dry_run=True)
        rec = _make_rec(role="analyst", recommended_value="claude-haiku-4-5-20251001")
        applicator = ConfigApplicator()

        new_settings, results = applicator.apply(settings, [rec], policy)

        assert len(results) == 1
        assert results[0].status == AdaptationStatus.DRY_RUN
        # Model should NOT have changed
        assert new_settings.model_analyst == settings.model_analyst


class TestImmutability:
    def test_never_mutates_original_settings(self) -> None:
        settings = AppSettings()
        original_model = settings.model_analyst
        policy = _enabled_policy()
        rec = _make_rec(role="analyst", recommended_value="totally-new-model")
        applicator = ConfigApplicator()

        new_settings, _results = applicator.apply(settings, [rec], policy)

        # Original settings must be untouched
        assert settings.model_analyst == original_model
        # New settings should have the change
        assert new_settings.model_analyst == "totally-new-model"


class TestAudit:
    def test_audit_trail_written(self, tmp_path: Path) -> None:
        audit_path = tmp_path / "audit.ndjson"
        writer = AppendOnlyAuditWriter(audit_path)
        settings = AppSettings()
        policy = _enabled_policy()
        rec = _make_rec()
        applicator = ConfigApplicator(audit_writer=writer)

        applicator.apply(settings, [rec], policy)

        entries = writer.read_all()
        assert len(entries) == 1
        assert entries[0].category == AuditCategory.CONFIG_CHANGE
        assert entries[0].actor == "config_applicator"
        assert "analyst" in entries[0].detail

    def test_no_audit_writer_ok(self) -> None:
        settings = AppSettings()
        policy = _enabled_policy()
        rec = _make_rec()
        applicator = ConfigApplicator(audit_writer=None)

        # Should not raise
        new_settings, results = applicator.apply(settings, [rec], policy)
        assert len(results) == 1
        assert results[0].status == AdaptationStatus.APPLIED


class TestEdgeCases:
    def test_empty_recommendations(self) -> None:
        settings = AppSettings()
        policy = _enabled_policy()
        applicator = ConfigApplicator()

        new_settings, results = applicator.apply(settings, [], policy)

        assert results == []
        assert new_settings.model_analyst == settings.model_analyst

    def test_unknown_parameter_skipped(self) -> None:
        settings = AppSettings()
        policy = _enabled_policy()
        rec = _make_rec(parameter="temperature")
        applicator = ConfigApplicator()

        new_settings, results = applicator.apply(settings, [rec], policy)

        # Unknown parameter is silently skipped — not in results
        assert len(results) == 0
