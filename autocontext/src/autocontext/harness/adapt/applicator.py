"""ConfigApplicator — applies ConfigRecommendations to AppSettings with safety guardrails."""

from __future__ import annotations

import re
from typing import TYPE_CHECKING

from autocontext.harness.adapt.types import AdaptationPolicy, AdaptationResult, AdaptationStatus
from autocontext.harness.audit.types import AuditCategory, AuditEntry
from autocontext.harness.audit.writer import AppendOnlyAuditWriter
from autocontext.harness.meta.types import ConfigRecommendation

if TYPE_CHECKING:
    from autocontext.config.settings import AppSettings

# Maps role name to the AppSettings field for its model.
_MODEL_FIELD_MAP: dict[str, str] = {
    "competitor": "model_competitor",
    "analyst": "model_analyst",
    "coach": "model_coach",
    "architect": "model_architect",
    "curator": "model_curator",
    "translator": "model_translator",
}


def _parse_cadence(value: str) -> int | None:
    """Extract the upper-bound integer from a cadence description.

    E.g. "every 2-3 generations" -> 3, "every 5 generations" -> 5.
    Returns None if no digits are found.
    """
    nums = re.findall(r"\d+", value)
    if not nums:
        return None
    return int(nums[-1])


class ConfigApplicator:
    """Applies ConfigRecommendations to AppSettings with safety guardrails."""

    def __init__(self, audit_writer: AppendOnlyAuditWriter | None = None) -> None:
        self._audit_writer = audit_writer

    def apply(
        self,
        settings: AppSettings,
        recommendations: list[ConfigRecommendation],
        policy: AdaptationPolicy,
    ) -> tuple[AppSettings, list[AdaptationResult]]:
        """Apply recommendations to settings, returning (new_settings, results).

        Never mutates the input *settings*; uses ``model_copy(update={...})``.
        """
        results: list[AdaptationResult] = []
        updates: dict[str, object] = {}
        changes_applied = 0

        for rec in recommendations:
            # Skip unknown parameters silently (not in results)
            if rec.parameter not in policy.allowed_parameters:
                continue

            # Determine the target field name and the new value
            field_name: str | None = None
            new_value: object = None

            if rec.parameter == "model":
                field_name = _MODEL_FIELD_MAP.get(rec.role)
                if field_name is None:
                    continue  # unknown role — skip silently
                new_value = rec.recommended_value
            elif rec.parameter == "cadence":
                field_name = "architect_every_n_gens"
                parsed = _parse_cadence(rec.recommended_value)
                if parsed is None:
                    continue  # unparseable — skip silently
                new_value = parsed
            else:
                # Allowed but unhandled parameter — skip silently
                continue

            previous_value = str(getattr(settings, field_name))

            # Policy checks (order matters: disabled > confidence > max_changes > dry_run)
            if not policy.enabled:
                result = AdaptationResult(
                    timestamp=AdaptationResult.now(),
                    role=rec.role,
                    parameter=rec.parameter,
                    previous_value=previous_value,
                    new_value=str(new_value),
                    confidence=rec.confidence,
                    rationale=rec.rationale,
                    status=AdaptationStatus.SKIPPED_DISABLED,
                )
            elif rec.confidence < policy.min_confidence:
                result = AdaptationResult(
                    timestamp=AdaptationResult.now(),
                    role=rec.role,
                    parameter=rec.parameter,
                    previous_value=previous_value,
                    new_value=str(new_value),
                    confidence=rec.confidence,
                    rationale=rec.rationale,
                    status=AdaptationStatus.SKIPPED_LOW_CONFIDENCE,
                )
            elif changes_applied >= policy.max_changes_per_cycle:
                result = AdaptationResult(
                    timestamp=AdaptationResult.now(),
                    role=rec.role,
                    parameter=rec.parameter,
                    previous_value=previous_value,
                    new_value=str(new_value),
                    confidence=rec.confidence,
                    rationale=rec.rationale,
                    status=AdaptationStatus.SKIPPED_MAX_CHANGES,
                )
            elif policy.dry_run:
                result = AdaptationResult(
                    timestamp=AdaptationResult.now(),
                    role=rec.role,
                    parameter=rec.parameter,
                    previous_value=previous_value,
                    new_value=str(new_value),
                    confidence=rec.confidence,
                    rationale=rec.rationale,
                    status=AdaptationStatus.DRY_RUN,
                )
            else:
                updates[field_name] = new_value
                changes_applied += 1
                result = AdaptationResult(
                    timestamp=AdaptationResult.now(),
                    role=rec.role,
                    parameter=rec.parameter,
                    previous_value=previous_value,
                    new_value=str(new_value),
                    confidence=rec.confidence,
                    rationale=rec.rationale,
                    status=AdaptationStatus.APPLIED,
                )

            results.append(result)
            self._write_audit(result)

        new_settings = settings.model_copy(update=updates) if updates else settings.model_copy()
        return new_settings, results

    def _write_audit(self, result: AdaptationResult) -> None:
        if self._audit_writer is None:
            return
        entry = AuditEntry(
            timestamp=result.timestamp,
            category=AuditCategory.CONFIG_CHANGE,
            actor="config_applicator",
            action=f"{result.status.value}:{result.parameter}",
            detail=f"{result.role} {result.parameter}: {result.previous_value} -> {result.new_value}",
            metadata=result.to_dict(),
        )
        self._audit_writer.append(entry)
