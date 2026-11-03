"""Feedback-conditioned CPU fixture generation through autocontext's provider API.

This module deliberately accepts only its pinned literal fixture provider. The
normal LLMProvider/OutputSchema and refinement-prompt interfaces are exercised,
but no model, SDK, endpoint, subprocess, or generated program is executed.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any, Literal, Self

from pydantic import Field, model_validator

from autocontext.loop.refinement_prompt import build_refinement_prompt
from autocontext.providers.base import CompletionResult, LLMProvider, OutputSchema, ProviderError
from autocontext.weight_evolution.artifacts import canonical_digest, file_identity
from autocontext.weight_evolution.models import Digest, FrozenModel, _unique_object
from autocontext.weight_evolution.session_models import (
    AdapterResult,
    CandidateState,
    EditOperation,
    InspectOperation,
    Mechanism,
    PracticeProblem,
    ProbeOutput,
    SessionAdapterRequest,
    SessionDecoding,
    SessionUsage,
)

MAX_PROPOSAL_BYTES = 16384
MAX_GENERATION_PROMPT_BYTES = 256 * 1024
FIXTURE_MODEL = "offline-feedback-fixture-v1"
FIXTURE_MAX_TOKENS = 4096  # Interface setting only: output accounting remains UTF-8 bytes.
SYSTEM_PROMPT = "Return one JSON edit proposal matching the supplied schema. Use only the supplied practice feedback."
_VIEW_START = "\n<weight_edit_generation_view>\n"
_VIEW_END = "\n</weight_edit_generation_view>"


def _encoded(value: object) -> str:
    return json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=False, allow_nan=False)


def _json_object(text: str, *, max_bytes: int) -> dict[str, Any]:
    if not isinstance(text, str) or len(text.encode("utf-8")) > max_bytes:
        raise ValueError("fixture JSON exceeds its bounded byte size")
    value = json.loads(text, object_pairs_hook=_unique_object)
    _encoded(value)
    if not isinstance(value, dict):
        raise ValueError("fixture JSON must be an object")
    return value


class _DigestModel(FrozenModel):
    @property
    def digest(self) -> str:
        return canonical_digest(self.to_dict())


class GenerationFeedback(_DigestModel):
    problem_id: str = Field(min_length=1, max_length=512)
    score: int = Field(ge=0, le=1)
    response_sha256: Digest


class GenerationView(_DigestModel):
    mechanism: Mechanism
    state: CandidateState
    practice: tuple[PracticeProblem, ...] = Field(min_length=1, max_length=1000)
    feedback: tuple[GenerationFeedback, ...] = Field(default=(), max_length=1000)

    @model_validator(mode="after")
    def unique_practice_feedback(self) -> Self:
        ids = tuple(row.problem_id for row in self.practice)
        feedback_ids = tuple(row.problem_id for row in self.feedback)
        if len(set(ids)) != len(ids) or len(set(feedback_ids)) != len(feedback_ids):
            raise ValueError("generation view repeats a practice or feedback ID")
        if not set(feedback_ids) <= set(ids):
            raise ValueError("generation feedback must belong to the selected practice prompts")
        return self


class EditProposal(_DigestModel):
    mechanism: Mechanism
    rationale: str = Field(default="", max_length=2048)
    context_text: str | None = Field(default=None, max_length=8192)
    weight_descriptor: str | None = Field(default=None, min_length=1, max_length=4096)
    decoding: SessionDecoding | None = None

    @model_validator(mode="after")
    def one_allowed_surface(self) -> Self:
        field = {"C": "context_text", "W": "weight_descriptor", "D": "decoding"}[self.mechanism]
        if getattr(self, field) is None or sum(value is not None for value in (
            self.context_text, self.weight_descriptor, self.decoding,
        )) != 1:
            raise ValueError("proposal must change exactly its mechanism's allowed surface")
        return self

    def to_edit(self, *, operation_id: str, candidate_digest: str, reservation: SessionUsage) -> EditOperation:
        validated = EditProposal.model_validate_json(_encoded(self.to_dict()))
        return EditOperation(operation_id=operation_id, candidate_digest=candidate_digest, reservation=reservation,
                             context_text=validated.context_text, weight_descriptor=validated.weight_descriptor,
                             decoding=validated.decoding)


class ProposalReceipt(_DigestModel):
    execution_kind: Literal["fixture"] = "fixture"
    model_execution_performed: Literal[False] = False
    gpu_execution_deferred: Literal[True] = True
    proposal: EditProposal
    model: str = Field(min_length=1, max_length=256)
    constrained: bool
    stop_reason: str | None = Field(default=None, max_length=128)
    served_model: str | None = Field(default=None, max_length=256)
    provider_input_bytes: int = Field(gt=0, le=1000000000)
    provider_output_bytes: int = Field(ge=0, le=1000000000)


def parse_proposal_receipt(message: str, mechanism: Mechanism | None = None) -> ProposalReceipt:
    receipt = ProposalReceipt.model_validate_json(_encoded(_json_object(message, max_bytes=MAX_PROPOSAL_BYTES)))
    if mechanism is not None and receipt.proposal.mechanism != mechanism:
        raise ValueError("proposal receipt belongs to another mechanism")
    return receipt


def generation_view(mechanism: Mechanism, request: SessionAdapterRequest) -> GenerationView:
    request = SessionAdapterRequest.model_validate_json(_encoded(request.to_dict()))
    current = {row.problem_id: row for row in request.feedback if row.candidate_digest == request.candidate.digest}
    feedback = tuple(GenerationFeedback(problem_id=problem.problem_id, score=current[problem.problem_id].score,
                                        response_sha256=current[problem.problem_id].response_sha256)
                     for problem in request.practice if problem.problem_id in current)
    return GenerationView(mechanism=mechanism, state=request.candidate.state,
                          practice=request.practice, feedback=feedback)


def generation_prompt(view: GenerationView) -> str:
    view = GenerationView.model_validate_json(_encoded(view.to_dict()))
    prompt = build_refinement_prompt(
        scenario_rules=f"CPU fixture editing. Change only the {view.mechanism} mechanism's typed surface.",
        strategy_interface=_encoded(EditProposal.model_json_schema()),
        evaluation_criteria="Improve the complete practice cohort. Scores are fixture evidence, not benchmark claims.",
        parent_strategy=_encoded(view.state.to_dict()),
        match_feedback=_encoded([row.to_dict() for row in view.feedback]),
    )
    prompt += "\nPut any reasoning in the proposal's rationale field; return JSON only."
    prompt += _VIEW_START + _encoded(view.to_dict()) + _VIEW_END
    if len(prompt.encode("utf-8")) > MAX_GENERATION_PROMPT_BYTES:
        raise ValueError("generation prompt exceeds its bounded byte size")
    return prompt


def _prompt_view(prompt: str) -> GenerationView:
    if len(prompt.encode("utf-8")) > MAX_GENERATION_PROMPT_BYTES or not prompt.endswith(_VIEW_END):
        raise ValueError("generation prompt is missing its bounded view")
    before, marker, encoded = prompt.rpartition(_VIEW_START)
    if not marker or not before:
        raise ValueError("generation prompt is missing its view")
    view = GenerationView.model_validate_json(_encoded(_json_object(encoded[:-len(_VIEW_END)],
                                                                    max_bytes=MAX_GENERATION_PROMPT_BYTES)))
    if generation_prompt(view) != prompt:
        raise ValueError("generation prompt differs from its exact projected view")
    return view


class FeedbackRoute(_DigestModel):
    view_digest: Digest
    response_text: str = Field(default="", max_length=32768)
    outcome: Literal["return", "failure", "interruption"] = "return"
    message: str = Field(default="", max_length=2048)
    constrained: bool = False
    stop_reason: str | None = Field(default="stop", max_length=128)
    served_model: str | None = Field(default=None, max_length=256)


class ProbeFixture(_DigestModel):
    state_digest: Digest
    outputs: tuple[ProbeOutput, ...] = Field(default=(), max_length=1000)
    outcome: Literal["return", "failure", "interruption"] = "return"
    message: str = Field(default="", max_length=2048)

    @model_validator(mode="after")
    def output_scope(self) -> Self:
        if (self.outcome == "return") != bool(self.outputs):
            raise ValueError("only successful probe fixtures require outputs")
        if len({row.problem_id for row in self.outputs}) != len(self.outputs):
            raise ValueError("probe fixture repeats a problem ID")
        return self


class AdaptiveFixtureInterrupted(KeyboardInterrupt):
    """An ambiguous fixture dispatch; M6 retains its claim and blocks retries."""


def _sources() -> dict[str, dict[str, object]]:
    package = Path(__file__).parent
    sources = {"adaptive_provider.py": package / "adaptive_provider.py",
               "session_models.py": package / "session_models.py",
               "providers/base.py": package.parent / "providers" / "base.py",
               "loop/refinement_prompt.py": package.parent / "loop" / "refinement_prompt.py"}
    return {name: file_identity(path, max_bytes=1024 * 1024) for name, path in sources.items()}


class OfflineFeedbackProvider(LLMProvider):
    """A normal provider interface backed solely by literal, view-addressed routes."""

    execution_kind = "fixture"

    def __init__(self, routes: tuple[FeedbackRoute, ...]) -> None:
        if not 1 <= len(routes) <= 10000:
            raise ValueError("fixture provider requires a bounded nonempty route catalog")
        self.routes = tuple(FeedbackRoute.model_validate_json(_encoded(route.to_dict())) for route in routes)
        if len({route.view_digest for route in self.routes}) != len(self.routes):
            raise ValueError("fixture provider repeats a generation view")

    @property
    def supports_single_dispatch(self) -> bool:
        return True

    def default_model(self) -> str:
        return FIXTURE_MODEL

    def provenance(self) -> dict[str, Any]:
        return {"execution_kind": "fixture", "model_execution_performed": False, "gpu_execution_deferred": True,
                "model": self.default_model(), "sources": _sources(),
                "routes": [route.to_dict() for route in self.routes], "selection": "exact-practice-view-digest"}

    @property
    def identity_digest(self) -> str:
        return canonical_digest(self.provenance())

    def complete(self, system_prompt: str, user_prompt: str, model: str | None = None,
                 temperature: float = 0.0, max_tokens: int = 4096,
                 output_schema: OutputSchema | None = None) -> CompletionResult:
        if (system_prompt != SYSTEM_PROMPT or model not in (None, self.default_model()) or temperature != 0.0
                or max_tokens != FIXTURE_MAX_TOKENS or output_schema is None
                or output_schema.name != "weight_edit_proposal"
                or canonical_digest(output_schema.schema) != canonical_digest(EditProposal.model_json_schema())):
            raise ProviderError("fixture provider call differs from the frozen generation interface")
        view = _prompt_view(user_prompt)
        route = next((route for route in self.routes if route.view_digest == view.digest), None)
        if route is None:
            raise ProviderError("no literal proposal is pinned for this exact practice feedback/state")
        if route.outcome == "failure":
            raise ProviderError(route.message or "scripted proposal failure")
        if route.outcome == "interruption":
            raise AdaptiveFixtureInterrupted(route.message or "scripted ambiguous proposal dispatch")
        return CompletionResult(text=route.response_text, model=self.default_model(), constrained=route.constrained,
                                stop_reason=route.stop_reason, served_model=route.served_model)


class ProviderSessionAdapter:
    """M6 adapter: generation inside claimed inspections, typed edits, literal probes."""

    execution_kind = "fixture"

    def __init__(self, mechanism: Mechanism, provider: LLMProvider, probes: tuple[ProbeFixture, ...]) -> None:
        if mechanism not in ("C", "W", "D"):
            raise ValueError("invalid editing mechanism")
        if type(provider) is not OfflineFeedbackProvider or provider.supports_single_dispatch is not True:
            raise ValueError("adaptive sessions accept only the pinned single-dispatch offline fixture provider")
        if not 1 <= len(probes) <= 10000:
            raise ValueError("adaptive sessions require a bounded nonempty probe catalog")
        self.mechanism = mechanism
        self.provider = provider
        self.probes = tuple(ProbeFixture.model_validate_json(_encoded(probe.to_dict())) for probe in probes)
        if len({probe.state_digest for probe in self.probes}) != len(self.probes):
            raise ValueError("adaptive probe catalog repeats a candidate state")

    def provenance(self) -> dict[str, Any]:
        return {"execution_kind": "fixture", "model_execution_performed": False, "gpu_execution_deferred": True,
                "mechanism": self.mechanism, "provider_digest": self.provider.identity_digest,
                "probes": [probe.to_dict() for probe in self.probes], "sources": _sources()}

    @property
    def identity_digest(self) -> str:
        return canonical_digest(self.provenance())

    def execute(self, request: SessionAdapterRequest) -> AdapterResult:
        request = SessionAdapterRequest.model_validate_json(_encoded(request.to_dict()))
        operation = request.operation
        if isinstance(operation, InspectOperation):
            if operation.target != "practice":
                raise ValueError("adaptive proposal inspection requires practice prompts")
            view = generation_view(self.mechanism, request)
            prompt = generation_prompt(view)
            schema = OutputSchema(name="weight_edit_proposal", schema=EditProposal.model_json_schema())
            call = {"system_prompt": SYSTEM_PROMPT, "user_prompt": prompt, "model": self.provider.default_model(),
                    "temperature": 0.0, "max_tokens": FIXTURE_MAX_TOKENS,
                    "output_schema": {"name": schema.name, "schema": schema.schema}}
            input_bytes = len(_encoded(call).encode("utf-8"))
            if input_bytes > operation.reservation.editor_input_bytes:
                raise ValueError("transformed provider input exceeds the reserved editor byte budget")
            completion = self.provider.complete(
                system_prompt=SYSTEM_PROMPT, user_prompt=prompt, model=self.provider.default_model(),
                temperature=0.0, max_tokens=FIXTURE_MAX_TOKENS,
                output_schema=schema,
            )
            output_bytes = len(completion.text.encode("utf-8"))
            if output_bytes > operation.reservation.editor_output_bytes:
                raise ValueError("raw provider output exceeds the reserved editor byte budget")
            if completion.stop_reason in ("length", "max_tokens", "max_output_tokens"):
                raise ValueError("truncated generation cannot become an edit proposal")
            proposal = EditProposal.model_validate_json(_encoded(_json_object(completion.text,
                                                                             max_bytes=MAX_PROPOSAL_BYTES)))
            if proposal.mechanism != self.mechanism:
                raise ValueError("provider proposed a different mechanism")
            field = {"C": "context_text", "W": "weight_descriptor", "D": "decoding"}[self.mechanism]
            if getattr(proposal, field) == getattr(request.candidate.state, field):
                raise ValueError("provider proposed a no-op")
            receipt = ProposalReceipt(proposal=proposal, model=completion.model or self.provider.default_model(),
                                      constrained=completion.constrained, stop_reason=completion.stop_reason,
                                      served_model=completion.served_model,
                                      provider_input_bytes=input_bytes, provider_output_bytes=output_bytes)
            message = _encoded(receipt.to_dict())
            parse_proposal_receipt(message, self.mechanism)
            return AdapterResult(operation_digest=operation.digest, message=message, simulated_seconds=0.01)
        if isinstance(operation, EditOperation):
            EditProposal(mechanism=self.mechanism, context_text=operation.context_text,
                         weight_descriptor=operation.weight_descriptor, decoding=operation.decoding)
            return AdapterResult(operation_digest=operation.digest, message="typed edit acknowledged", simulated_seconds=0.01)
        probe = next((probe for probe in self.probes if probe.state_digest == request.candidate.state.digest), None)
        if probe is None:
            raise ValueError("candidate state is absent from the pinned practice probe catalog")
        if probe.outcome == "failure":
            raise ProviderError(probe.message or "scripted probe infrastructure failure")
        if probe.outcome == "interruption":
            raise AdaptiveFixtureInterrupted(probe.message or "scripted ambiguous probe dispatch")
        if tuple(row.problem_id for row in probe.outputs) != operation.problem_ids:
            raise ValueError("probe catalog does not exactly cover the requested practice IDs")
        return AdapterResult(operation_digest=operation.digest, probe_outputs=probe.outputs,
                             message="literal practice probe", simulated_seconds=0.01)
