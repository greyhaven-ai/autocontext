"""AC-937: record what Python's ``extract_json`` does, so TypeScript can replay it.

TypeScript has no shared model-JSON extractor. Five hand-rolled fence regexes
live in ``ts/src``, all subtly different, and the one that was examined turned
out to carry a live scoring defect (AC-924). Porting ``extract_json`` is the fix;
this file is what makes the port verifiable instead of hopeful.

The corpus is organised by the RULE each case exercises rather than by input
shape, because the rules are the thing that must survive the port. Several were
regressions once already and the reasons are recorded in
``harness/core/output_parser.py``: choosing the wrong fence (C1) had a silent
wrong-answer mode, and substituting nearby JSON for a broken payload is AC-921.

Outcomes record the exception TYPE, never its message. Python and JavaScript
disagree about wording, and pinning wording would make the fixture fail on a
difference nobody cares about while hiding the ones that matter.

Regenerate with:
    uv run --frozen python scripts/generate_model_json_parity_fixtures.py
Check without writing:
    uv run --frozen python scripts/generate_model_json_parity_fixtures.py --check
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path
from typing import Any

REPO = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO / "src"))

FIXTURE_PATH = REPO.parent / "docs" / "model-json-extraction-parity-fixtures.json"

BOM = chr(0xFEFF)

# Each case: name -> (text, kwargs). Grouped by the rule under test.
CORPUS: dict[str, dict[str, tuple[str, dict[str, Any]]]] = {
    "direct": {
        "bare_object": ('{"a": 1}', {}),
        "object_with_prose_around": ('Here you go:\n{"a": 1}\nhope that helps', {}),
        "json_tagged_fence": ('```json\n{"a": 1}\n```', {}),
        "untagged_fence": ('```\n{"a": 1}\n```', {}),
        "same_line_fence": ('```json{"a": 1}```', {}),
        "uppercase_tag": ('```JSON\n{"a": 1}\n```', {}),
        "nested_object": ('{"a": {"b": {"c": 1}}}', {}),
        "object_containing_braces_in_strings": ('{"a": "a } brace", "b": "{"}', {}),
    },
    "fence_selection": {
        # C1: a reasoning block before the answer must not become the scope.
        "reasoning_fence_then_json_fence": (
            'Let me think.\n```\nreasoning scratch\n```\nHere:\n```json\n{"a": 1}\n```',
            {},
        ),
        # The silent-wrong-answer shape: preamble fence PARSES as an object.
        "python_fence_with_dict_then_json_fence": (
            '```python\nd = {"scratch": 1}\n```\n```json\n{"a": 1}\n```',
            {},
        ),
        # AC-920: first tagged block wins when there are two.
        "two_json_fences_first_wins": ('```json\n{"a": 1}\n```\n```json\n{"a": 2}\n```', {}),
        # AC-921: a corrupt tagged block fails closed rather than substituting.
        "corrupt_tagged_fence_does_not_fall_back_to_prose": (
            '```json\n{"a": oops\n```\nlater {"decoy": true}',
            {"on_failure": "none"},
        ),
        "untagged_fences_tried_in_order": ('```\nno braces here\n```\n```\n{"a": 1}\n```', {}),
        # Brace-free fences mean there is no fenced payload; scan the whole text.
        "brace_free_fence_falls_back_to_prose": ('```\njust prose\n```\n{"a": 1}', {}),
        # jsonl/json5 share a prefix with json but are different info strings.
        "jsonl_tag_is_not_a_json_tag": ('```jsonl\n{"a": 1}\n```\n```json\n{"a": 2}\n```', {}),
        "json5_tag_is_not_a_json_tag": ('```json5\n{"a": 1}\n```\n```json\n{"a": 2}\n```', {}),
    },
    "recovery": {
        "prose_wrapped_bare_json": ('blah {"a": 1} blah', {}),
        "two_bare_objects_first_wins": ('{"a": 1} then {"a": 2}', {}),
        "earlier_malformed_candidate_is_skipped": ('{oops} then {"a": 1}', {}),
        # Documented divergence: the fenced rescue is a crude first-{ to last-},
        # so this recovers unfenced but NOT fenced.
        "malformed_then_valid_unfenced": ('blah {oops} and {"a": 1}', {"on_failure": "none"}),
        "malformed_then_valid_fenced": ('```\nblah {oops} and {"a": 1}\n```', {"on_failure": "none"}),
        # Forces the span scan to respect JSON string quoting. Without prose
        # around it the whole scope parses directly and the scanner is never
        # consulted, so a naive brace counter survives -- found by mutating the
        # TypeScript port and watching this rule NOT fail.
        "brace_inside_string_needs_span_scan": ('note {"a": "} brace"} end', {}),
    },
    "arrays": {
        "top_level_array_is_wrong_type": ("[1, 2, 3]", {"on_failure": "none"}),
        "fenced_array_is_wrong_type": ('```json\n[{"a": 1}]\n```', {"on_failure": "none"}),
        "truncated_array_does_not_unwrap_inner_object": ('[{"a": 1}, {"b": 2}', {"on_failure": "none"}),
        "prose_then_truncated_array_is_terminal": ('here: [{"a": 1}', {"on_failure": "none"}),
        # Position-sensitive, and easy to get wrong when porting. A `[` at index
        # 0 makes the scope's shape decisive: the whole scope is the only
        # candidate and object rescue never runs. Prose in front of the bracket
        # changes that -- the bracket is then just text to skip past.
        "leading_bracket_prose_is_terminal": ('[draft] {"a": 1}', {"on_failure": "none"}),
        "leading_citation_bracket_is_terminal": ('[1] see also [2, 3] {"a": 1}', {"on_failure": "none"}),
        "prose_then_markdown_bracket_then_object": ('Use [draft] while reasoning, then return {"a": 1}', {}),
        "prose_then_markdown_link_then_object": (
            'See [working notes](https://example.test/notes), then return {"a": 1}',
            {},
        ),
        "prose_then_numeric_citation_then_object": ('Sources [1, 2] support the result {"a": 1}', {}),
        # The wrong-type rule stops the WHOLE scan, not just the current scope.
        # An array in the first fence must not be stepped over to reach an
        # object in a later one. Every other array case fails for a second
        # reason too, so this is the only one that isolates the rule.
        "array_fence_before_object_fence_is_terminal": (
            '```\n[1, 2]\n```\n```\n{"a": 1}\n```',
            {"on_failure": "none"},
        ),
    },
    "bom": {
        "bom_before_object": (f'{BOM}{{"a": 1}}', {}),
        "bom_inside_fence": (f'```json\n{BOM}{{"a": 1}}\n```', {}),
    },
    "require_unique": {
        "competing_objects_rejected": ('{"a": 1} and {"b": 2}', {"require_unique": True, "on_failure": "none"}),
        "tagged_answer_trusted_despite_scratch": (
            '```\n{"scratch": 1}\n```\n```json\n{"a": 1}\n```',
            {"require_unique": True},
        ),
        "single_object_accepted": ('{"a": 1}', {"require_unique": True}),
    },
    "required_keys": {
        "earlier_ineligible_object_is_skipped": (
            '{"metadata": {"request_id": "abc"}} then {"score": 1e-1}',
            {"required_keys": ["score"]},
        ),
        "missing_required_key_returns_none": (
            '{"metadata": true}',
            {"required_keys": ["score"], "on_failure": "none"},
        ),
        "uniqueness_ignores_ineligible_objects": (
            '{"metadata": true} then {"score": 0.8}',
            {"required_keys": ["score"], "require_unique": True},
        ),
        "competing_eligible_objects_are_rejected": (
            '{"score": 0.2} then {"score": 0.8}',
            {"required_keys": ["score"], "require_unique": True, "on_failure": "none"},
        ),
    },
    "failure_policy": {
        "no_json_raises": ("no json here at all", {}),
        "no_json_returns_none": ("no json here at all", {"on_failure": "none"}),
        "empty_string_returns_none": ("", {"on_failure": "none"}),
        # AC-922: the failed-decode cap keeps degenerate input bounded.
        "degenerate_repetition_returns_none": ("{" * 500, {"on_failure": "none"}),
    },
}


def _outcome(text: str, kwargs: dict[str, Any]) -> dict[str, Any]:
    from autocontext.harness.core.output_parser import extract_json

    try:
        value = extract_json(text, **kwargs)
    except Exception as exc:  # noqa: BLE001 - the type is the recorded outcome
        return {"status": "raised", "error_type": type(exc).__name__}
    if value is None:
        return {"status": "none"}
    return {"status": "ok", "value": value}


def build() -> dict[str, Any]:
    groups: dict[str, Any] = {}
    for group, cases in CORPUS.items():
        groups[group] = {
            name: {"text": text, "options": kwargs, "expected": _outcome(text, kwargs)} for name, (text, kwargs) in cases.items()
        }
    return {
        "contract": (
            "AC-937 model-JSON extraction parity. Generated from Python's "
            "harness/core/output_parser.py::extract_json; TypeScript replays this file. "
            "Outcomes record the exception TYPE, not its message, because the two languages "
            "word errors differently and pinning wording would fail on a difference nobody cares about."
        ),
        "generator": "autocontext/scripts/generate_model_json_parity_fixtures.py",
        "groups": groups,
    }


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--check", action="store_true", help="fail if the fixture is stale instead of writing it")
    args = parser.parse_args()

    payload = build()
    rendered = json.dumps(payload, indent=2, ensure_ascii=False) + "\n"

    if args.check:
        if not FIXTURE_PATH.exists():
            print(f"MISSING: {FIXTURE_PATH}")
            return 1
        if FIXTURE_PATH.read_text(encoding="utf-8") != rendered:
            print(f"DRIFT: {FIXTURE_PATH} differs from what the Python parser produces. Regenerate with:")
            print("  uv run --frozen python scripts/generate_model_json_parity_fixtures.py")
            return 1
        return 0

    FIXTURE_PATH.write_text(rendered, encoding="utf-8")
    total = sum(len(cases) for cases in payload["groups"].values())
    print(f"wrote {FIXTURE_PATH} ({total} cases across {len(payload['groups'])} groups)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
