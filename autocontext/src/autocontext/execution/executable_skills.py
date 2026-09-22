"""Explicit bundle-to-skill bridge for one pure schema migration (AC-1028).

Evaluation and serving share assembly, applicability, execution and verification.
Only serving additionally requires the existing durable active promotion record.
This module neither routes requests nor promotes candidates.
"""

from __future__ import annotations

import hashlib
import json
import platform
import threading
import time
from pathlib import Path
from typing import Any, Literal

import pydantic
from pydantic import ConfigDict, Field, model_validator

from autocontext.artifacts import policy_candidate
from autocontext.artifacts.models import ArtifactProvenance
from autocontext.artifacts.policy_candidate import CandidateLimits, CandidateManifestBase, FrozenContract, json_payload
from autocontext.context_bundles import models as bundle_models
from autocontext.context_bundles.models import BundleComponent, ComponentKind, ContextBundle, stable_digest
from autocontext.context_bundles.store import ContextBundleStore
from autocontext.context_bundles.store_transactions import promotion_from_pointer
from autocontext.execution import docker_isolation, docker_skill
from autocontext.execution.docker_skill import DockerSkillExecutor
from autocontext.kernel_evolution import _process_control
from autocontext.knowledge.harness_entries import SkillReference
from autocontext.runtime_images import PINNED_PYTHON_RUNTIME_IMAGE

SCENARIO = "schema_migration"
CONTRACT = "profile-v1-to-v2"
COMPONENT_KEY = "schema_migration_skill"
DEFAULT_LIMITS = CandidateLimits(timeout_seconds=10, max_memory_mb=128)
APPLICABILITY = {"contract": CONTRACT, "schema_version": 1, "unknown_fields": "abstain"}


class ProfileV1(FrozenContract):
    model_config = ConfigDict(frozen=True, extra="forbid", strict=True)
    schema_version: int = Field(ge=1)
    name: str = Field(min_length=1, max_length=1024)
    enabled: bool


class ProfileV2(FrozenContract):
    model_config = ConfigDict(frozen=True, extra="forbid", strict=True)
    schema_version: int = Field(ge=2, le=2)
    display_name: str = Field(min_length=1, max_length=1024)
    status: Literal["enabled", "disabled"]


def _json(value: str) -> Any:
    def pairs(items: list[tuple[str, Any]]) -> dict[str, Any]:
        result: dict[str, Any] = {}
        for key, item in items:
            if key in result:
                raise ValueError("duplicate JSON key")
            result[key] = item
        return result

    def constant(value: str) -> None:
        raise ValueError(f"non-finite JSON constant: {value}")

    return json.loads(value, object_pairs_hook=pairs, parse_constant=constant)


class SkillSourceEvidence(FrozenContract):
    """Retained, replayable source evidence; never a claim of held-out quality."""

    artifact_id: str = Field(min_length=1, max_length=160)
    role: Literal["example", "counterexample"]
    content_json: str = Field(max_length=65536)
    content_sha256: str = Field(pattern=r"^[a-f0-9]{64}$")

    @model_validator(mode="after")
    def check_identity(self) -> SkillSourceEvidence:
        if json_payload(_json(self.content_json)) != self.content_json:
            raise ValueError("source evidence must be canonical JSON")
        if hashlib.sha256(self.content_json.encode()).hexdigest() != self.content_sha256:
            raise ValueError("source evidence digest mismatch")
        return self

    @classmethod
    def create(cls, artifact_id: str, role: Literal["example", "counterexample"], content: Any) -> SkillSourceEvidence:
        payload = json_payload(content)
        return cls(artifact_id=artifact_id, role=role, content_json=payload,
                   content_sha256=hashlib.sha256(payload.encode()).hexdigest())


class ExecutableSkillManifest(CandidateManifestBase):
    schema_version: Literal["autocontext.executable-skill.v1"] = "autocontext.executable-skill.v1"
    runtime: Literal["python3.11;docker-json-skill-v1"] = "python3.11;docker-json-skill-v1"
    runtime_image: str = PINNED_PYTHON_RUNTIME_IMAGE
    source_evidence: tuple[SkillSourceEvidence, ...] = Field(min_length=2, max_length=32)

    @model_validator(mode="after")
    def require_supported_environment(self) -> ExecutableSkillManifest:
        if self.runtime_image != PINNED_PYTHON_RUNTIME_IMAGE or self.limits.max_memory_mb < 64:
            raise ValueError("unsupported runtime image or memory limit")
        if {row.role for row in self.source_evidence} != {"example", "counterexample"}:
            raise ValueError("source evidence requires an example and counterexample")
        if len({row.artifact_id for row in self.source_evidence}) != len(self.source_evidence):
            raise ValueError("duplicate source evidence identity")
        if len(self.skill.source.encode()) > 65536:
            raise ValueError("candidate source exceeds 64 KiB")
        return self


class ExecutableSkillEligibility(FrozenContract):
    # Merely being a TOOL_SPEC is deliberately insufficient.
    kind: Literal["executable_skill_candidate"]
    eligible: bool = Field(strict=True)
    manifest_digest: str = Field(pattern=r"^[a-f0-9]{64}$")
    manifest_json: str

    @model_validator(mode="after")
    def require_eligible(self) -> ExecutableSkillEligibility:
        if not self.eligible:
            raise ValueError("helper is not eligible for executable conversion")
        return self


def evaluator_identity() -> str:
    """Invalidate replay on changes to the verifier, bridge or isolation boundary."""
    paths = [Path(__file__), *(Path(str(module.__file__)) for module in (
        docker_skill, docker_isolation, _process_control, policy_candidate, bundle_models,
    ))]
    return stable_digest({
        "source": {path.name: hashlib.sha256(path.read_bytes()).hexdigest() for path in paths},
        "verifier_python": platform.python_version(), "pydantic": pydantic.__version__,
    })


def propose_schema_migration(
    store: ContextBundleStore, skill: SkillReference, *, source_evidence: tuple[SkillSourceEvidence, ...],
    run_id: str, limits: CandidateLimits = DEFAULT_LIMITS,
) -> ContextBundle:
    """Explicitly enroll one helper as an inactive candidate in existing storage."""
    baseline = store.active_bundle(SCENARIO)
    if baseline is None:
        raise ValueError("an existing baseline is required; the bridge never bootstraps live state")
    identity = evaluator_identity()
    if baseline.evaluator_epoch != identity:
        raise ValueError("baseline evaluator epoch is incompatible")
    manifest = ExecutableSkillManifest(
        scenario=SCENARIO, scenario_version=CONTRACT,
        input_schema=json_payload(ProfileV1.model_json_schema()), output_schema=json_payload(ProfileV2.model_json_schema()),
        applicability=json_payload(APPLICABILITY), skill_json=json_payload(skill),
        provenance_json=json_payload(ArtifactProvenance(run_id=run_id, generation=0, scenario=SCENARIO)),
        limits=limits, source_evidence=source_evidence,
        synthesis_provider="explicit-helper", requested_model="none", served_model=None,
        prompt_version="no-synthesis", prompt_sha256=hashlib.sha256(b"").hexdigest(),
        evaluator_identity=identity, evaluation_refs=(), synthesis_usage_json='{"model_calls":0}',
    )
    record = ExecutableSkillEligibility(kind="executable_skill_candidate", eligible=True,
                                        manifest_digest=manifest.digest, manifest_json=json_payload(manifest))
    components = [c for c in baseline.components if not (c.kind == ComponentKind.TOOL_SPEC and c.key == COMPONENT_KEY)]
    components.append(BundleComponent.json(ComponentKind.TOOL_SPEC, COMPONENT_KEY, record.model_dump(mode="json")))
    bundle = ContextBundle.create(scenario=SCENARIO, evaluator_epoch=identity, parent_digest=baseline.digest,
                                  components=components)
    store.propose(bundle, source_run_id=run_id, source_generation=0, rationale="Explicit isolated schema migration candidate")
    return bundle


def inspect_executable_skill(store: ContextBundleStore, bundle_digest: str) -> ExecutableSkillManifest:
    bundle = store.load_bundle(SCENARIO, bundle_digest)
    if bundle.digest != bundle_digest:
        raise ValueError("bundle artifact digest mismatch")
    components = [c for c in bundle.components_of_kind(ComponentKind.TOOL_SPEC) if c.key == COMPONENT_KEY]
    if len(components) != 1 or components[0].media_type != "application/json":
        raise ValueError("reference-only helper has no explicit executable eligibility record")
    record = ExecutableSkillEligibility.model_validate(_json(components[0].content))
    manifest = ExecutableSkillManifest.model_validate(_json(record.manifest_json))
    manifest.require_digest(record.manifest_digest)
    if bundle.evaluator_epoch != manifest.evaluator_identity:
        raise ValueError("bundle evaluator does not match the skill manifest")
    return manifest


class SkillInvocation(FrozenContract):
    status: Literal["success", "abstention", "execution_failure", "verification_failure"]
    reason: str
    output_json: str | None = None
    bundle_digest: str
    artifact_digest: str | None = None
    source_sha256: str | None = None
    input_sha256: str
    environment_digest: str | None = None
    image_identity: str | None = None
    evaluator_identity: str
    mode: Literal["evaluation", "serving"]
    model_calls: Literal[0] = 0
    execution_seconds: float = 0
    elapsed_seconds: float
    limits_json: str | None = None


def _require_active(store: ContextBundleStore, bundle_digest: str, identity: str) -> None:
    pointer = store.active_pointer(SCENARIO)
    if pointer is None or pointer.get("bundle_digest") != bundle_digest or pointer.get("evaluator_epoch") != identity:
        raise ValueError("skill bundle is not active under this evaluator")
    promotion_from_pointer(store, SCENARIO, pointer)


def invoke_executable_skill(
    store: ContextBundleStore, bundle_digest: str, input_json: str, *,
    mode: Literal["evaluation", "serving"], caller_limits: CandidateLimits = DEFAULT_LIMITS,
    executor: DockerSkillExecutor | None = None, cancel: threading.Event | None = None,
) -> SkillInvocation:
    """Return a verified proposal, never apply changes to an external artifact.

The caller pins a bundle digest for both incumbent/candidate evaluation and live
use. Inactive evaluation is explicit. There is no default evaluation bypass for
serving, LLM completion dependency, automatic activation or fallback execution.
"""
    if mode not in ("evaluation", "serving"):
        raise ValueError("explicit evaluation or serving mode is required")
    started = time.monotonic()
    identity = evaluator_identity()
    metadata: dict[str, Any] = {
        "bundle_digest": bundle_digest, "input_sha256": hashlib.sha256(input_json.encode(errors="surrogatepass")).hexdigest(),
        "evaluator_identity": identity, "mode": mode,
    }

    def result(status: Literal["success", "abstention", "execution_failure", "verification_failure"],
               reason: str, output_json: str | None = None) -> SkillInvocation:
        return SkillInvocation(status=status, reason=reason, output_json=output_json,
                               elapsed_seconds=time.monotonic() - started, **metadata)

    try:
        manifest = inspect_executable_skill(store, bundle_digest)
        metadata.update(artifact_digest=manifest.digest, source_sha256=manifest.source_sha256,
                        limits_json=json_payload(manifest.limits))
        manifest.limits.require_within(caller_limits)
        if (
            manifest.scenario != SCENARIO or manifest.scenario_version != CONTRACT
            or manifest.input_schema != json_payload(ProfileV1.model_json_schema())
            or manifest.output_schema != json_payload(ProfileV2.model_json_schema())
            or manifest.applicability != json_payload(APPLICABILITY) or manifest.evaluator_identity != identity
        ):
            raise ValueError("incompatible schema, applicability or evaluator identity")
        executor = executor or DockerSkillExecutor()
        if executor.image != manifest.runtime_image:
            raise ValueError("runtime dependency mismatch")
        metadata["environment_digest"] = stable_digest({
            "runtime": manifest.runtime, "image": manifest.runtime_image, "limits": manifest.limits.model_dump(),
            "dependencies": manifest.dependencies, "capability_grants": manifest.capability_grants,
            "evaluator": identity,
        })
        if mode == "serving":
            _require_active(store, bundle_digest, identity)
        if cancel is not None and cancel.is_set():
            return result("execution_failure", "cancelled")
        try:
            if len(input_json.encode()) > 65536:
                return result("abstention", "input_limit")
            data = _json(input_json)
            profile = ProfileV1.model_validate(data)
            normalized_input = json_payload(profile)
        except (ValueError, TypeError, RecursionError):
            return result("abstention", "invalid_input")
        if profile.schema_version != 1:
            return result("abstention", "unsupported_schema_version")
        execution = executor.execute(manifest.skill.source, normalized_input, manifest.limits, cancel=cancel)
        metadata.update(execution_seconds=execution.elapsed_seconds, image_identity=execution.image_identity)
        if execution.failure:
            return result("execution_failure", execution.failure)
        if cancel is not None and cancel.is_set():
            return result("execution_failure", "cancelled")
        try:
            output = ProfileV2.model_validate(_json(execution.output))
            expected = ProfileV2(schema_version=2, display_name=profile.name,
                                 status="enabled" if profile.enabled else "disabled")
            if output != expected:
                return result("verification_failure", "migration_postcondition_failed")
            output_json = json_payload(output)
        except (ValueError, TypeError, RecursionError):
            return result("verification_failure", "invalid_output")
        # Detect durable artifact drift and revocation during a long invocation.
        if inspect_executable_skill(store, bundle_digest).digest != manifest.digest:
            raise ValueError("artifact changed during execution")
        if mode == "serving":
            _require_active(store, bundle_digest, identity)
        return result("success", "verified_proposal", output_json)
    except (OSError, ValueError, TypeError, RecursionError, RuntimeError) as exc:
        return result("execution_failure", str(exc)[:512])
