from __future__ import annotations

import ast
import importlib.util
import sys
import tempfile
from pathlib import Path
from unittest.mock import MagicMock, patch

from autocontext.scenarios.custom.agent_task_spec import AgentTaskSpec

_VALID_OUTPUT_FORMATS = {"free_text", "json_schema", "code"}

_DATA_REFERENCE_PATTERNS = [
    "you will be provided with",
    "given the following data",
    "analyze the following",
    "using the provided",
    "based on the data below",
]


def validate_spec(spec: AgentTaskSpec) -> list[str]:
    """Validate an AgentTaskSpec for completeness and correctness."""
    errors: list[str] = []

    if not spec.task_prompt or not spec.task_prompt.strip():
        errors.append("task_prompt must not be empty")

    if not spec.judge_rubric or not spec.judge_rubric.strip():
        errors.append("judge_rubric must not be empty")

    if spec.output_format not in _VALID_OUTPUT_FORMATS:
        errors.append(
            f"output_format '{spec.output_format}' not in {_VALID_OUTPUT_FORMATS}"
        )

    if spec.reference_context is not None and not spec.reference_context.strip():
        errors.append("reference_context, if provided, must not be empty")

    if spec.required_concepts is not None:
        if not isinstance(spec.required_concepts, list):
            errors.append("required_concepts must be a list of strings")
        elif not spec.required_concepts:
            errors.append("required_concepts, if provided, must not be empty")
        else:
            for i, concept in enumerate(spec.required_concepts):
                if not isinstance(concept, str) or not concept.strip():
                    errors.append(f"required_concepts[{i}] must be a non-empty string")

    if spec.reference_sources is not None:
        if not isinstance(spec.reference_sources, list):
            errors.append("reference_sources must be a list of strings")
        elif not spec.reference_sources:
            errors.append("reference_sources, if provided, must not be empty")
        else:
            for i, source in enumerate(spec.reference_sources):
                if not isinstance(source, str) or not source.strip():
                    errors.append(f"reference_sources[{i}] must be a non-empty string")

    if spec.max_rounds < 1:
        errors.append("max_rounds must be >= 1")

    if not (0.0 < spec.quality_threshold <= 1.0):
        errors.append("quality_threshold must be between 0.0 (exclusive) and 1.0 (inclusive)")

    if spec.revision_prompt is not None and not spec.revision_prompt.strip():
        errors.append("revision_prompt, if provided, must not be empty")

    if spec.context_preparation is not None and not spec.context_preparation.strip():
        errors.append("context_preparation, if provided, must not be empty")

    if spec.required_context_keys is not None:
        if not isinstance(spec.required_context_keys, list):
            errors.append("required_context_keys must be a list of strings")
        elif not spec.required_context_keys:
            errors.append("required_context_keys, if provided, must not be empty")
        else:
            for i, key in enumerate(spec.required_context_keys):
                if not isinstance(key, str) or not key.strip():
                    errors.append(f"required_context_keys[{i}] must be a non-empty string")

    # Detect prompts that reference external data without providing sample_input
    if spec.sample_input is None:
        prompt_lower = spec.task_prompt.lower()
        for pattern in _DATA_REFERENCE_PATTERNS:
            if pattern in prompt_lower:
                errors.append(
                    f"task_prompt references external data ('{pattern}') but sample_input is None; "
                    "set sample_input to provide the data that will be embedded in the prompt"
                )
                break

    return errors


def validate_syntax(source: str) -> list[str]:
    """Validate that generated source code parses without syntax errors."""
    errors: list[str] = []
    try:
        ast.parse(source)
    except SyntaxError as exc:
        errors.append(f"syntax error at line {exc.lineno}: {exc.msg}")
    return errors


def validate_execution(source: str) -> list[str]:
    """Validate by importing and instantiating the generated class."""
    errors: list[str] = []
    try:
        tree = ast.parse(source)
        for node in ast.walk(tree):
            if isinstance(node, ast.Call) and getattr(node.func, "id", None) == "LLMJudge":
                if any(keyword.arg == "llm_fn" for keyword in node.keywords):
                    errors.append(
                        "evaluate_output uses legacy llm_fn wiring; use provider= with runtime provider resolution"
                    )
                    break
    except SyntaxError:
        # Syntax issues are handled by validate_syntax().
        pass

    with tempfile.TemporaryDirectory() as tmp:
        mod_path = Path(tmp) / "agent_task_mod.py"
        mod_path.write_text(source, encoding="utf-8")

        mod_name = f"_agent_task_validation_{id(source)}"
        spec = importlib.util.spec_from_file_location(mod_name, str(mod_path))
        if spec is None or spec.loader is None:
            errors.append("could not create module spec from source")
            return errors

        mod = importlib.util.module_from_spec(spec)
        try:
            sys.modules[mod_name] = mod
            spec.loader.exec_module(mod)  # type: ignore[union-attr]
        except Exception as exc:
            errors.append(f"import failed: {exc}")
            return errors
        finally:
            sys.modules.pop(mod_name, None)

        # Find the AgentTaskInterface subclass
        from autocontext.scenarios.agent_task import AgentTaskInterface

        found_cls = None
        for attr_name in dir(mod):
            attr = getattr(mod, attr_name)
            if (
                isinstance(attr, type)
                and issubclass(attr, AgentTaskInterface)
                and attr is not AgentTaskInterface
            ):
                found_cls = attr
                break

        if found_cls is None:
            errors.append("no AgentTaskInterface subclass found in generated code")
            return errors

        try:
            instance = found_cls()
        except Exception as exc:
            errors.append(f"instantiation failed: {exc}")
            return errors

        try:
            prompt = instance.get_task_prompt({})
            if not prompt:
                errors.append("get_task_prompt() returned empty string")
        except Exception as exc:
            errors.append(f"get_task_prompt() raised: {exc}")

        try:
            rubric = instance.get_rubric()
            if not rubric:
                errors.append("get_rubric() returned empty string")
        except Exception as exc:
            errors.append(f"get_rubric() raised: {exc}")

        # Validate prepare_context and validate_context if present
        prepared: dict = {}
        try:
            state = instance.initial_state()
            prepared = instance.prepare_context(state)
            if not isinstance(prepared, dict):
                errors.append("prepare_context() must return a dict")
                prepared = {}
        except Exception as exc:
            errors.append(f"prepare_context() raised: {exc}")

        try:
            ctx_errors = instance.validate_context(prepared)
            if not isinstance(ctx_errors, list):
                errors.append("validate_context() must return a list")
        except Exception as exc:
            errors.append(f"validate_context() raised: {exc}")

        try:
            mock_settings = MagicMock()
            mock_settings.judge_model = "configured-judge-model"
            mock_provider = MagicMock()
            mock_provider.default_model.return_value = "provider-default-model"
            mock_result = MagicMock()
            mock_result.score = 0.5
            mock_result.reasoning = "validator smoke test"
            mock_result.dimension_scores = {}
            mock_result.internal_retries = 0

            with (
                patch("autocontext.config.load_settings", return_value=mock_settings),
                patch("autocontext.providers.registry.get_provider", return_value=mock_provider),
                patch("autocontext.execution.judge.LLMJudge.evaluate", return_value=mock_result),
            ):
                eval_result = instance.evaluate_output("validator smoke output", prepared)
                if not hasattr(eval_result, "score"):
                    errors.append("evaluate_output() did not return an AgentTaskResult-like object")
        except Exception as exc:
            errors.append(f"evaluate_output() raised: {exc}")

    return errors
