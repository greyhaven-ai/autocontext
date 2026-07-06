"""resolve a charter target's eval_suite name to a held-out suite loaded from disk.

The evaluate stage distinguishes two absences: a missing suite file means no
held-out suite was ever defined for the target (load returns None), while a
present-but-empty file (or one whose lines are all malformed) means a suite
exists but has nothing to score yet (load returns an EvalSuite with [] cases).
Callers key different behaviour off that distinction, so it must be preserved.
"""

from __future__ import annotations

import json
import os
from dataclasses import dataclass
from pathlib import Path


@dataclass(slots=True)
class EvalCase:
    prompt: str
    reference: str = ""


@dataclass(slots=True)
class EvalSuite:
    name: str
    cases: list[EvalCase]


def load_eval_suite(suites_dir: Path, name: str) -> EvalSuite | None:
    """Load ``suites_dir/<name>.jsonl`` into an EvalSuite, or None if absent.

    ``name`` is a charter eval_suite string (e.g. "competitor_holdout"); it must
    name a file directly inside suites_dir, so a path separator or a ".."
    traversal sequence is rejected with ValueError rather than allowed to escape.
    Each non-blank line is a JSON object; a line that fails to parse or lacks a
    non-empty "prompt" string is skipped. A missing file returns None; a present
    file always returns an EvalSuite (with [] cases when nothing parsed).
    """
    if os.sep in name or (os.altsep and os.altsep in name) or ".." in name:
        raise ValueError(f"eval_suite name must not contain a path separator or '..': {name!r}")

    path = suites_dir / f"{name}.jsonl"
    if not path.exists():
        return None

    cases: list[EvalCase] = []
    for line in path.read_text(encoding="utf-8").splitlines():
        if not line.strip():
            continue
        try:
            obj = json.loads(line)
        except json.JSONDecodeError:
            continue
        prompt = obj.get("prompt") if isinstance(obj, dict) else None
        if not isinstance(prompt, str) or not prompt:
            continue
        reference = obj.get("reference", "")
        cases.append(EvalCase(prompt=prompt, reference=reference if isinstance(reference, str) else ""))

    return EvalSuite(name=name, cases=cases)
