from __future__ import annotations

import ast
import importlib.util
import logging
import re
import sys
import tempfile
from pathlib import Path
from unittest.mock import MagicMock, patch

from autocontext.scenarios.custom.agent_task_spec import AgentTaskSpec

logger = logging.getLogger(__name__)

_VALID_OUTPUT_FORMATS = {"free_text", "json_schema", "code"}

# Words too common to signal domain intent.
_INTENT_STOP_WORDS = frozenset({
    "a", "an", "the", "and", "or", "of", "for", "to", "in", "on", "at", "by",
    "is", "are", "was", "be", "do", "does", "it", "we", "they", "i", "you",
    "that", "can", "should", "could", "would", "will", "must", "with", "which",
    "what", "how", "task", "agent", "system", "create", "build", "write", "make",
    "good", "well", "very", "just", "also", "clear", "structured", "want", "need",
})

# Task-family keyword clusters — if description keywords fall in one cluster
# but the spec's keywords fall in a different one, that signals drift.
_TASK_FAMILIES: dict[str, frozenset[str]] = {
    "code": frozenset({
        "code", "coding", "python", "function", "algorithm", "program", "debug",
        "debugging", "syntax", "compile", "runtime", "api", "endpoint", "scraper",
        "refactor", "test", "tests", "testing", "unittest", "bug", "bugs",
        "implementation", "implement", "software", "developer", "class", "method",
    }),
    "writing": frozenset({
        "essay", "article", "blog", "write", "writing", "prose", "paragraph",
        "narrative", "story", "fiction", "poetry", "haiku", "poem", "literary",
        "persuasive", "rhetoric", "composition", "draft", "editorial", "recipe",
        "cookbook", "cooking", "ingredients", "frosting", "cake", "baking",
    }),
    "analysis": frozenset({
        "analysis", "analyze", "diagnostic", "diagnose", "investigate", "root",
        "cause", "debugging", "logs", "monitoring", "crash", "error", "incident",
        "forensic", "audit", "trace", "profiling", "performance", "bottleneck",
    }),
    "data": frozenset({
        "data", "dataset", "classification", "classifier", "sentiment", "nlp",
        "machine", "learning", "model", "training", "prediction", "regression",
        "clustering", "neural", "deep", "statistics", "statistical", "inference",
    }),
    "design": frozenset({
        "architecture", "design", "pattern", "microservices", "distributed",
        "scalability", "infrastructure", "devops", "deployment", "kubernetes",
        "docker", "cloud", "aws", "system", "systems",
    }),
}

# Signals that the description is asking for code generation output.
_CODE_INTENT_SIGNALS = frozenset({
    "code", "function", "class", "algorithm", "program", "implement",
    "script", "python", "javascript", "typescript", "java", "rust", "go",
    "generate code", "write code", "coding", "scraper", "web scraper",
})

# Counter-signals: when present alongside code keywords, the task is about
# evaluating/reviewing code (text output), not generating code.
_CODE_EVALUATION_SIGNALS = frozenset({
    "evaluate", "review", "assess", "analyze", "analyse", "audit", "quality",
    "correctness", "diagnostic", "diagnose", "critique", "score", "grade",
})

# Signals that the description is asking for text/writing output.
_TEXT_INTENT_SIGNALS = frozenset({
    "essay", "article", "blog", "story", "write about", "persuasive",
    "narrative", "poem", "haiku", "report", "documentation", "recipe",
})

# Signals that the description is asking for a structured JSON-shaped output.
_JSON_INTENT_SIGNALS = frozenset({
    "json", "json schema", "structured output", "structured response",
    "return a schema", "return schema", "fields", "field names", "key value",
    "key-value", "object with", "array of", "machine readable", "machine-readable",
})

# Patterns that ALWAYS indicate external data (future/passive voice referring
# to data the system must supply).
_ALWAYS_EXTERNAL_PATTERNS = [
    "you will be provided with",
]

# Patterns that reference data which MAY be inline — only flag as external
# when the prompt does NOT contain substantial inline data after the phrase.
_CONTEXTUAL_DATA_PATTERNS = [
    "given the following data",
    "analyze the following",
    "using the provided",
    "based on the data below",
]

# Markers that signal structured inline data.
_INLINE_DATA_MARKERS = ("{", "[", "|", "- ", "* ", "##", "```")
_INLINE_DATA_MIN_CHARS = 50
_KEY_VALUE_LINE_RE = re.compile(r"^[A-Za-z0-9 _()/.-]{1,40}:\s+\S")
_CSV_LINE_RE = re.compile(r"^[^,\n]+(?:,[^,\n]+)+$")
_INLINE_BLOCK_RE = re.compile(r"^[^.\n]{0,80}:\s*\n", re.DOTALL)


def _has_inline_data_after(prompt: str, pattern: str) -> bool:
    """Check if actual inline payload data follows a data-reference phrase."""
    idx = prompt.lower().find(pattern)
    if idx < 0:
        return False
    after = prompt[idx + len(pattern):].strip()
    if not after:
        return False

    lines = [line.strip() for line in after.splitlines() if line.strip()]

    if any(line.startswith(_INLINE_DATA_MARKERS) for line in lines):
        return True

    key_value_lines = [line for line in lines if _KEY_VALUE_LINE_RE.match(line)]
    if len(key_value_lines) >= 2:
        return True

    csv_lines = [line for line in lines if _CSV_LINE_RE.match(line)]
    if len(csv_lines) >= 2:
        return True

    match = _INLINE_BLOCK_RE.match(after)
    if match is not None:
        payload = after[match.end():].strip()
        if len(payload) >= _INLINE_DATA_MIN_CHARS:
            return True

    return False


def _extract_keywords(text: str) -> set[str]:
    """Extract meaningful keywords from text, excluding stop words."""
    words = re.sub(r"[^a-z0-9\s]", " ", text.lower()).split()
    return {w for w in words if w not in _INTENT_STOP_WORDS and len(w) > 1}


def _detect_task_family(keywords: set[str]) -> str | None:
    """Return the best-matching task family for a set of keywords, or None."""
    best_family: str | None = None
    best_overlap = 0
    for family, family_words in _TASK_FAMILIES.items():
        overlap = len(keywords & family_words)
        if overlap > best_overlap:
            best_overlap = overlap
            best_family = family
    return best_family if best_overlap >= 1 else None


def _fuzzy_overlap(a: set[str], b: set[str], min_prefix: int = 4) -> set[str]:
    """Find keywords that overlap exactly or share a common prefix (≥min_prefix chars).

    Handles common morphological variants like "log"/"logs", "analysis"/"analyze".
    """
    matched: set[str] = set()
    for word_a in a:
        if word_a in b:
            matched.add(word_a)
            continue
        if len(word_a) >= min_prefix:
            for word_b in b:
                if len(word_b) >= min_prefix:
                    shorter = min(len(word_a), len(word_b))
                    prefix_len = max(min_prefix, shorter - 2)
                    if word_a[:prefix_len] == word_b[:prefix_len]:
                        matched.add(word_a)
                        break
    return matched


def validate_intent(
    user_description: str,
    spec: AgentTaskSpec,
) -> list[str]:
    """Validate that the generated spec matches the user's original intent.

    Checks for:
    1. Task-family drift (description domain vs spec domain)
    2. Keyword overlap (core domain terms preserved in spec)
    3. Output format compatibility
    """
    if not user_description or not user_description.strip():
        return []

    errors: list[str] = []
    desc_lower = user_description.lower()
    desc_keywords = _extract_keywords(user_description)
    spec_keywords = _extract_keywords(spec.task_prompt + " " + spec.judge_rubric)

    # --- 1. Task-family drift ---
    desc_family = _detect_task_family(desc_keywords)
    spec_family = _detect_task_family(spec_keywords)
    if desc_family and spec_family and desc_family != spec_family:
        errors.append(
            f"intent mismatch: description suggests '{desc_family}' task family "
            f"but generated spec resembles '{spec_family}'"
        )

    # --- 2. Keyword overlap ---
    if desc_keywords and spec_keywords:
        overlap = _fuzzy_overlap(desc_keywords, spec_keywords)
        overlap_ratio = len(overlap) / len(desc_keywords) if desc_keywords else 1.0
        if overlap_ratio == 0 and len(desc_keywords) >= 2:
            errors.append(
                "intent drift: no domain keywords from the description appear "
                "in the generated task prompt or rubric"
            )

    # --- 3. Output format compatibility ---
    desc_signals_code = any(sig in desc_lower for sig in _CODE_INTENT_SIGNALS)
    desc_signals_text = any(sig in desc_lower for sig in _TEXT_INTENT_SIGNALS)
    desc_signals_code_eval = any(sig in desc_lower for sig in _CODE_EVALUATION_SIGNALS)
    desc_signals_json = any(sig in desc_lower for sig in _JSON_INTENT_SIGNALS)

    # Only flag code→free_text mismatch when the description asks for code
    # *generation*, not code *evaluation/review* (which produces text output).
    if desc_signals_code and not desc_signals_text and not desc_signals_code_eval and spec.output_format == "free_text":
        errors.append(
            "format mismatch: description implies code output but "
            "spec uses output_format='free_text'"
        )
    if desc_signals_text and not desc_signals_code and spec.output_format == "code":
        errors.append(
            "format mismatch: description implies text output but "
            "spec uses output_format='code'"
        )
    if desc_signals_json and spec.output_format != "json_schema":
        errors.append(
            "format mismatch: description implies structured JSON output but "
            f"spec uses output_format='{spec.output_format}'"
        )

    return errors


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

    # Detect prompts that reference external data without providing sample_input.
    # Patterns are split into "always external" (hard fail) and "contextual"
    # (only fail when the prompt does NOT contain inline data after the phrase).
    if spec.sample_input is None:
        prompt_lower = spec.task_prompt.lower()
        for pattern in _ALWAYS_EXTERNAL_PATTERNS:
            if pattern in prompt_lower:
                errors.append(
                    f"task_prompt references external data ('{pattern}') but sample_input is None; "
                    "set sample_input to provide the data that will be embedded in the prompt"
                )
                break
        else:
            for pattern in _CONTEXTUAL_DATA_PATTERNS:
                if pattern in prompt_lower and not _has_inline_data_after(spec.task_prompt, pattern):
                    errors.append(
                        f"task_prompt references data ('{pattern}') but sample_input is None "
                        "and no substantial inline data follows the reference; "
                        "either embed the data inline or set sample_input"
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
            logger.debug("scenarios.custom.agent_task_validator: caught Exception", exc_info=True)
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
            logger.debug("scenarios.custom.agent_task_validator: caught Exception", exc_info=True)
            errors.append(f"instantiation failed: {exc}")
            return errors

        try:
            prompt = instance.get_task_prompt({})
            if not prompt:
                errors.append("get_task_prompt() returned empty string")
        except Exception as exc:
            logger.debug("scenarios.custom.agent_task_validator: caught Exception", exc_info=True)
            errors.append(f"get_task_prompt() raised: {exc}")

        try:
            rubric = instance.get_rubric()
            if not rubric:
                errors.append("get_rubric() returned empty string")
        except Exception as exc:
            logger.debug("scenarios.custom.agent_task_validator: caught Exception", exc_info=True)
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
            logger.debug("scenarios.custom.agent_task_validator: caught Exception", exc_info=True)
            errors.append(f"prepare_context() raised: {exc}")

        try:
            ctx_errors = instance.validate_context(prepared)
            if not isinstance(ctx_errors, list):
                errors.append("validate_context() must return a list")
        except Exception as exc:
            logger.debug("scenarios.custom.agent_task_validator: caught Exception", exc_info=True)
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
            logger.debug("scenarios.custom.agent_task_validator: caught Exception", exc_info=True)
            errors.append(f"evaluate_output() raised: {exc}")

    return errors
