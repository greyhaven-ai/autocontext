from __future__ import annotations

import re

from autocontext.scenarios.custom.artifact_editing_spec import ArtifactEditingSpec


def _class_name(name: str) -> str:
    parts = re.split(r"[^a-zA-Z0-9]+", name)
    return "".join(part.capitalize() for part in parts if part) + "ArtifactEditing"


def generate_artifact_editing_class(spec: ArtifactEditingSpec, name: str) -> str:
    class_name = _class_name(name)
    artifacts = ",\n".join(
        "            Artifact("
        f"path={artifact.path!r}, "
        f"content={artifact.content!r}, "
        f"content_type={artifact.content_type!r}, "
        f"metadata={artifact.metadata!r})"
        for artifact in spec.artifacts
    )
    return f'''from __future__ import annotations

import json
import re

from autocontext.scenarios.artifact_editing import (
    Artifact,
    ArtifactEditingInterface,
    ArtifactEditingResult,
    ArtifactValidationResult,
)


class {class_name}(ArtifactEditingInterface):
    name = {name!r}
    _validation_rules = {spec.validation_rules!r}

    def describe_task(self) -> str:
        return {spec.task_description!r}

    def get_rubric(self) -> str:
        return {spec.rubric!r}

    def initial_artifacts(self, seed: int | None = None) -> list[Artifact]:
        return [
{artifacts}
        ]

    def get_edit_prompt(self, artifacts: list[Artifact]) -> str:
        rendered = json.dumps([artifact.to_dict() for artifact in artifacts], indent=2)
        rules = "\\n".join(f"- {{rule}}" for rule in self._validation_rules)
        return (
            f"{{self.describe_task()}}\\n\\n"
            f"Artifacts:\\n{{rendered}}\\n\\n"
            f"Validation rules:\\n{{rules}}\\n\\n"
            'Return JSON with shape {{"artifacts": [{{"path": "...", "content": "...", "content_type": "..."}}]}} '
            "containing the full edited artifact set."
        )

    def _rules_for_path(self, path: str) -> list[str]:
        relevant: list[str] = []
        for rule in self._validation_rules:
            if " must " in rule:
                prefix, _ = rule.split(" must ", 1)
                if "/" in prefix and prefix.strip() != path:
                    continue
            relevant.append(rule)
        return relevant

    def _extract_snippets(self, rule: str) -> list[str]:
        return [match[0] or match[1] for match in re.findall(r'"([^"]+)"|\\'([^\\']+)\\'', rule)]

    def validate_artifact(self, artifact: Artifact) -> ArtifactValidationResult:
        errors: list[str] = []
        warnings: list[str] = []
        if not artifact.content.strip():
            errors.append(f"{{artifact.path}} must not be empty")
        for rule in self._rules_for_path(artifact.path):
            snippets = self._extract_snippets(rule)
            if not snippets:
                continue
            if "must not contain" in rule:
                for snippet in snippets:
                    if snippet in artifact.content:
                        errors.append(f"{{artifact.path}} violates rule: {{rule}}")
            else:
                for snippet in snippets:
                    if snippet not in artifact.content:
                        errors.append(f"{{artifact.path}} violates rule: {{rule}}")
        return ArtifactValidationResult(valid=not errors, errors=errors, warnings=warnings)

    def evaluate_edits(self, original: list[Artifact], edited: list[Artifact]) -> ArtifactEditingResult:
        diffs = self.compute_diffs(original, edited)
        validations = [self.validate_artifact(artifact) for artifact in edited]
        valid_count = sum(1 for result in validations if result.valid)
        error_count = sum(len(result.errors) for result in validations)
        correctness = valid_count / max(len(edited), 1)
        change_score = 1.0 if diffs else 0.0
        baseline = max(len(original), 1)
        precision = 1.0 if len(diffs) <= baseline else max(0.2, 1.0 - ((len(diffs) - baseline) / baseline) * 0.2)
        score = round((correctness * 0.7) + (change_score * 0.15) + (precision * 0.15), 4)
        return ArtifactEditingResult(
            score=score,
            reasoning=f"Validated {{valid_count}} of {{len(edited)}} artifacts with {{len(diffs)}} tracked edits.",
            dimension_scores={{
                "correctness": round(correctness, 4),
                "change_completeness": round(change_score, 4),
                "precision": round(precision, 4),
            }},
            diffs=diffs,
            validation=ArtifactValidationResult(
                valid=error_count == 0,
                errors=[error for result in validations for error in result.errors],
                warnings=[warning for result in validations for warning in result.warnings],
            ),
            artifacts_modified=len(diffs),
            artifacts_valid=valid_count,
        )
'''
