"""Verbatim solve scenario builder (AC-734).

The default ``autoctx solve`` pipeline runs an LLM scenario designer that
truncates briefs and generalizes similar-shaped descriptions into shared
``task_prompt`` text. For long, detail-laden inputs (Lean lemma signatures,
Putnam problem statements, anything that survives only when preserved
char-for-char) this silently strips the discriminating content and the
agent ends up solving the wrong problem.

This module exposes a verbatim mode: the operator supplies the exact
``task_prompt`` text and the build skips the LLM designer entirely.
The compiled scenario routes through the same codegen + registry pipeline
as designed scenarios so SQLite logging, knowledge snapshots, and the
multi-generation runner all keep working.

Usage shape::

    request = VerbatimSolveRequest(
        description="prove convexHull_subset_stdTri",
        task_prompt="<full Lean lemma + proof sketch text>",
    )
    result = build_verbatim_solve_scenario(request, knowledge_root=root)
    # result.scenario_name is now in SCENARIO_REGISTRY
"""

from __future__ import annotations

import importlib.util
import logging
import sys
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from autocontext.knowledge.solver import SolveScenarioBuildResult
from autocontext.scenarios.agent_task import AgentTaskInterface
from autocontext.scenarios.custom.agent_task_codegen import generate_agent_task_class
from autocontext.scenarios.custom.agent_task_revision import (
    patch_legacy_generated_evaluate_output,
    patch_legacy_generated_revise_output,
)
from autocontext.scenarios.custom.agent_task_spec import AgentTaskSpec
from autocontext.scenarios.custom.agent_task_validator import validate_execution
from autocontext.scenarios.custom.family_pipeline import (
    validate_for_family,
    validate_source_for_family,
)
from autocontext.scenarios.custom.naming import derive_name as shared_derive_name
from autocontext.scenarios.custom.registry import CUSTOM_SCENARIOS_DIR
from autocontext.scenarios.families import get_family_marker
from autocontext.util.json_io import write_json

logger = logging.getLogger(__name__)


# Default rubric used when the operator does not supply one. Kept generic so
# verbatim-mode scenarios still get a sensible LLM judge baseline; users who
# want strict scoring should pass --rubric.
_DEFAULT_VERBATIM_JUDGE_RUBRIC = (
    "Score 0.0 to 1.0 based on whether the output completely and "
    "correctly satisfies the task prompt. Output 1.0 only when every "
    "explicit requirement in the task prompt is met. Output ONLY the "
    "score as a decimal number."
)


@dataclass(slots=True)
class VerbatimSolveRequest:
    """Operator-supplied inputs for a verbatim solve build.

    ``description`` is informational (used for the derived scenario name
    and for logging). ``task_prompt`` is the verbatim text the agent
    receives — it is not transformed, truncated, or LLM-rewritten.
    """

    description: str
    task_prompt: str
    judge_rubric: str = field(default="")
    name_override: str | None = None

    def __post_init__(self) -> None:
        if not self.task_prompt or not self.task_prompt.strip():
            raise ValueError(
                "VerbatimSolveRequest.task_prompt must not be empty — verbatim mode preserves the operator's exact prompt"
            )
        if not self.judge_rubric.strip():
            self.judge_rubric = _DEFAULT_VERBATIM_JUDGE_RUBRIC


def build_verbatim_solve_scenario(
    request: VerbatimSolveRequest,
    *,
    knowledge_root: Path,
) -> SolveScenarioBuildResult:
    """Compile and register an agent-task scenario from a verbatim request.

    Skips the LLM designer entirely: the spec's ``task_prompt`` is the
    operator's exact text. Routes through the existing codegen + registry
    pipeline so downstream tooling (SQLite, snapshots, GenerationRunner)
    sees a normal registered scenario.
    """
    name = request.name_override or shared_derive_name(request.description)

    spec = AgentTaskSpec(
        task_prompt=request.task_prompt,
        judge_rubric=request.judge_rubric,
    )
    return _compile_and_register_agent_task(
        spec=spec,
        name=name,
        knowledge_root=knowledge_root,
    )


def _compile_and_register_agent_task(
    *,
    spec: AgentTaskSpec,
    name: str,
    knowledge_root: Path,
) -> SolveScenarioBuildResult:
    """Shared codegen → validate → save → load → register pipeline.

    Mirrors steps 3–7 of :class:`AgentTaskCreator.create`. Both the
    LLM-design path and the verbatim path land here so we have one
    canonical compile-and-register routine — DRY for the parts that
    genuinely repeat.
    """
    # 3. Codegen
    source = generate_agent_task_class(spec, name=name)

    # 4. Validate generated source
    source_errors = validate_source_for_family("agent_task", source)
    if source_errors:
        raise ValueError(f"verbatim source validation failed: {'; '.join(source_errors)}")
    spec_errors = validate_for_family("agent_task", _spec_dict(spec))
    if spec_errors:
        raise ValueError(f"verbatim spec validation failed: {'; '.join(spec_errors)}")

    # 5. Validate execution
    exec_errors = validate_execution(source)
    if exec_errors:
        raise ValueError(f"verbatim execution validation failed: {'; '.join(exec_errors)}")

    # 6. Save
    custom_dir = knowledge_root / CUSTOM_SCENARIOS_DIR
    scenario_dir = custom_dir / name
    scenario_dir.mkdir(parents=True, exist_ok=True)

    scenario_file = scenario_dir / "agent_task.py"
    scenario_file.write_text(source, encoding="utf-8")

    spec_file = scenario_dir / "agent_task_spec.json"
    write_json(spec_file, _spec_dict(spec))

    type_file = scenario_dir / "scenario_type.txt"
    type_file.write_text(get_family_marker("agent_task"), encoding="utf-8")

    # 7. Load and register
    cls = _load_agent_task(custom_dir, name)
    from autocontext.scenarios import SCENARIO_REGISTRY

    SCENARIO_REGISTRY[name] = cls
    logger.info("registered verbatim agent task '%s'", name)

    return SolveScenarioBuildResult(
        scenario_name=name,
        family_name="agent_task",
        llm_classifier_fallback_used=False,
    )


def _spec_dict(spec: AgentTaskSpec) -> dict[str, Any]:
    """Serialize a spec to the on-disk dict shape expected by the validator.

    Mirrors the structure produced by :class:`AgentTaskCreator.create`.
    """
    payload: dict[str, Any] = {
        "task_prompt": spec.task_prompt,
        "judge_rubric": spec.judge_rubric,
        "output_format": spec.output_format,
        "judge_model": spec.judge_model,
        "difficulty_tiers": spec.difficulty_tiers,
    }
    if spec.reference_context is not None:
        payload["reference_context"] = spec.reference_context
    if spec.reference_sources is not None:
        payload["reference_sources"] = spec.reference_sources
    if spec.required_concepts is not None:
        payload["required_concepts"] = spec.required_concepts
    if spec.calibration_examples is not None:
        payload["calibration_examples"] = spec.calibration_examples
    if spec.context_preparation is not None:
        payload["context_preparation"] = spec.context_preparation
    if spec.required_context_keys is not None:
        payload["required_context_keys"] = spec.required_context_keys
    if spec.max_rounds != 1:
        payload["max_rounds"] = spec.max_rounds
    if spec.quality_threshold != 0.9:
        payload["quality_threshold"] = spec.quality_threshold
    if spec.revision_prompt is not None:
        payload["revision_prompt"] = spec.revision_prompt
    if spec.sample_input is not None:
        payload["sample_input"] = spec.sample_input
    return payload


def _load_agent_task(
    custom_dir: Path,
    name: str,
) -> type[AgentTaskInterface]:
    """Load a generated AgentTaskInterface class from disk and patch it.

    Identical in shape to ``AgentTaskCreator._load_agent_task``; kept
    here so the verbatim path is self-contained.
    """
    module_name = f"autocontext.scenarios.custom.generated.agent_task_{name}"
    source_path = custom_dir / name / "agent_task.py"

    if module_name in sys.modules:
        del sys.modules[module_name]

    mod_spec = importlib.util.spec_from_file_location(module_name, str(source_path))
    if mod_spec is None or mod_spec.loader is None:
        raise ImportError(f"cannot create module spec for {source_path}")

    mod = importlib.util.module_from_spec(mod_spec)
    sys.modules[module_name] = mod
    mod_spec.loader.exec_module(mod)

    for attr_name in dir(mod):
        attr = getattr(mod, attr_name)
        if isinstance(attr, type) and issubclass(attr, AgentTaskInterface) and attr is not AgentTaskInterface:
            attr = patch_legacy_generated_evaluate_output(attr, source_path)
            return patch_legacy_generated_revise_output(attr, source_path)

    raise ImportError(f"no AgentTaskInterface subclass found in {module_name}")
