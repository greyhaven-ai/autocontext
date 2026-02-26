from __future__ import annotations

import ast
import importlib.util
import sys
import tempfile
from pathlib import Path

from mts.scenarios.custom.agent_task_spec import AgentTaskSpec

_VALID_OUTPUT_FORMATS = {"free_text", "json_schema", "code"}


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

    if not spec.judge_model or not spec.judge_model.strip():
        errors.append("judge_model must not be empty")

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
        from mts.scenarios.agent_task import AgentTaskInterface

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

    return errors
