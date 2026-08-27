"""Canonical component-manifest diffs shared by promotion and attribution (AC-997)."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from autocontext.context_bundles.models import ContextBundle, stable_digest, utf16_sort_key

MANIFEST_DIFF_SCHEMA_VERSION = 1


@dataclass(frozen=True, slots=True)
class BundleManifestChange:
    component_kind: str
    component_key: str
    tested_component_digest: str | None
    comparison_component_digest: str | None

    def to_dict(self) -> dict[str, Any]:
        return {
            "component_kind": self.component_kind,
            "component_key": self.component_key,
            "tested_component_digest": self.tested_component_digest,
            "comparison_component_digest": self.comparison_component_digest,
        }

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> BundleManifestChange:
        return cls(
            component_kind=str(data["component_kind"]),
            component_key=str(data["component_key"]),
            tested_component_digest=(
                str(data["tested_component_digest"]) if data.get("tested_component_digest") is not None else None
            ),
            comparison_component_digest=(
                str(data["comparison_component_digest"]) if data.get("comparison_component_digest") is not None else None
            ),
        )


@dataclass(frozen=True, slots=True)
class ContextBundleManifestDiff:
    tested_bundle_digest: str
    comparison_bundle_digest: str
    evaluator_epoch: str
    changes: tuple[BundleManifestChange, ...]

    def _digest_payload(self) -> dict[str, Any]:
        return {
            "schema_version": MANIFEST_DIFF_SCHEMA_VERSION,
            "tested_bundle_digest": self.tested_bundle_digest,
            "comparison_bundle_digest": self.comparison_bundle_digest,
            "evaluator_epoch": self.evaluator_epoch,
            "changes": [change.to_dict() for change in self.changes],
        }

    @property
    def digest(self) -> str:
        return stable_digest(self._digest_payload())

    def to_dict(self) -> dict[str, Any]:
        return {**self._digest_payload(), "digest": self.digest}

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> ContextBundleManifestDiff:
        if data.get("schema_version") != MANIFEST_DIFF_SCHEMA_VERSION:
            raise ValueError("unsupported context bundle manifest diff schema version")
        raw_changes = data.get("changes")
        if not isinstance(raw_changes, list) or not all(isinstance(change, dict) for change in raw_changes):
            raise ValueError("context bundle manifest diff changes must be objects")
        result = cls(
            tested_bundle_digest=str(data["tested_bundle_digest"]),
            comparison_bundle_digest=str(data["comparison_bundle_digest"]),
            evaluator_epoch=str(data["evaluator_epoch"]),
            changes=tuple(BundleManifestChange.from_dict(change) for change in raw_changes),
        )
        if data.get("digest") != result.digest:
            raise ValueError("context bundle manifest diff digest mismatch")
        return result


def context_bundle_manifest_diff(
    tested: ContextBundle,
    comparison: ContextBundle,
) -> ContextBundleManifestDiff:
    if tested.scenario != comparison.scenario:
        raise ValueError("context bundle manifest diff requires the same scenario")
    if tested.evaluator_epoch != comparison.evaluator_epoch:
        raise ValueError("context bundle manifest diff requires the same evaluator epoch")
    tested_components = {(component.kind.value, component.key): component for component in tested.components}
    comparison_components = {(component.kind.value, component.key): component for component in comparison.components}
    identities = sorted(
        tested_components.keys() | comparison_components.keys(),
        key=lambda item: (utf16_sort_key(item[0]), utf16_sort_key(item[1])),
    )
    changes: list[BundleManifestChange] = []
    for kind, key in identities:
        tested_component = tested_components.get((kind, key))
        comparison_component = comparison_components.get((kind, key))
        tested_digest = tested_component.digest if tested_component is not None else None
        comparison_digest = comparison_component.digest if comparison_component is not None else None
        if tested_digest == comparison_digest:
            continue
        changes.append(
            BundleManifestChange(
                component_kind=kind,
                component_key=key,
                tested_component_digest=tested_digest,
                comparison_component_digest=comparison_digest,
            )
        )
    return ContextBundleManifestDiff(
        tested_bundle_digest=tested.digest,
        comparison_bundle_digest=comparison.digest,
        evaluator_epoch=tested.evaluator_epoch,
        changes=tuple(changes),
    )


__all__ = [
    "MANIFEST_DIFF_SCHEMA_VERSION",
    "BundleManifestChange",
    "ContextBundleManifestDiff",
    "context_bundle_manifest_diff",
]
