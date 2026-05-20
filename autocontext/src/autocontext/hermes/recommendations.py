"""AC-709: read-only recommendation surface for Hermes curator.

Takes a :class:`~autocontext.hermes.advisor.Advisor` (slice 1 ships
:class:`~autocontext.hermes.advisor.BaselineAdvisor`; slice 2 will add
trained backends) and a live :class:`~autocontext.hermes.inspection.HermesInventory`,
runs the advisor over each active skill's features, and returns a
list of :class:`Recommendation` rows.

Read-only contract (AC-709 invariant): the surface never writes to
``~/.hermes``. Curator stays the mutation owner. Recommendations
flow out as JSONL the operator (or another tool) can review and
apply.

Protected skills (``pinned`` true, or ``provenance in {bundled, hub}``)
are filtered out of the default output so a recommendation cannot be
mistakenly applied against upstream-owned or operator-pinned content.
``include_protected=True`` surfaces them anyway with
``status="protected"``, useful for audit but not for action.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from autocontext.hermes.advisor import Advisor, BaselineAdvisor, SkillFeatures
from autocontext.hermes.inspection import HermesInventory, HermesSkill

# Status values:
#  - ``actionable``: the advisor's prediction can be applied if the
#    operator agrees. Curator still owns whether to.
#  - ``protected``: surfaced only when ``include_protected=True``; the
#    advisor's prediction is informational. Pinned / bundled / hub
#    skills cannot be acted on by AC-709's recommendation flow.
_ACTIONABLE = "actionable"
_PROTECTED = "protected"


@dataclass(frozen=True, slots=True)
class Recommendation:
    """One advisor-suggested action on a single skill."""

    skill_name: str
    predicted_action: str
    confidence: str
    status: str
    features: SkillFeatures
    reason: str

    def to_dict(self) -> dict[str, Any]:
        return {
            "skill_name": self.skill_name,
            "predicted_action": self.predicted_action,
            "confidence": self.confidence,
            "status": self.status,
            "features": {
                "skill_name": self.features.skill_name,
                "state": self.features.state,
                "provenance": self.features.provenance,
                "pinned": self.features.pinned,
                "use_count": self.features.use_count,
                "view_count": self.features.view_count,
                "patch_count": self.features.patch_count,
                "activity_count": self.features.activity_count,
            },
            "reason": self.reason,
        }


def recommend(
    *,
    inventory: HermesInventory,
    advisor: Advisor,
    include_protected: bool = False,
    reason: str | None = None,
) -> list[Recommendation]:
    """Run ``advisor`` against every active skill in ``inventory``.

    By default returns recommendations only for unprotected skills
    (not pinned, not bundled, not hub). ``include_protected`` flips
    the gate so protected skills appear with ``status="protected"``.

    ``reason`` is the human-readable rationale attached to every
    Recommendation. For the baseline advisor the caller passes
    something like ``"baseline majority class (X)"``; trained
    advisors will pass top feature contributions or similar.
    """
    rationale = reason if reason is not None else _default_reason(advisor)
    out: list[Recommendation] = []
    for skill in inventory.skills:
        protected = _is_protected(skill)
        if protected and not include_protected:
            continue
        features = _features_from_skill(skill)
        predicted = advisor.predict(features)
        out.append(
            Recommendation(
                skill_name=skill.name,
                predicted_action=predicted,
                confidence="advisory",
                status=_PROTECTED if protected else _ACTIONABLE,
                features=features,
                reason=rationale,
            )
        )
    return out


def _is_protected(skill: HermesSkill) -> bool:
    """Match the AC-705 dataset-export protection rules: pinned or
    upstream-owned provenance is off-limits as a mutation target."""
    return skill.pinned or skill.provenance in {"bundled", "hub"}


def _features_from_skill(skill: HermesSkill) -> SkillFeatures:
    """Project a :class:`HermesSkill` into the inference-time shape."""
    return SkillFeatures(
        skill_name=skill.name,
        state=skill.state,
        provenance=skill.provenance,
        pinned=skill.pinned,
        use_count=skill.use_count,
        view_count=skill.view_count,
        patch_count=skill.patch_count,
    )



def _default_reason(advisor: Advisor) -> str:
    """Pick a sensible per-advisor rationale when the caller did not
    pass one. BaselineAdvisor gets a specific message so reviewers
    can tell at a glance the recommendation is majority-class noise
    until a trained backend lands."""
    if isinstance(advisor, BaselineAdvisor):
        return f"baseline majority class ({advisor.majority_label})"
    return "advisor prediction"

__all__ = ["Recommendation", "recommend"]
