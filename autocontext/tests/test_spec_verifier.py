"""Tests for AC-772 pre-flight attack-model spec verifier.

Five concerns under test, each isolated (DDD):
  1. ``parse_invariants`` — extract structured Invariants from an LLM response.
  2. ``render_invariants`` — emit the ``## Solution invariants`` prompt block.
  3. ``InvariantCache`` — persist derived invariants by spec hash.
  4. ``derive_invariants`` — end-to-end orchestration (LLM call + parse + cache).
  5. ``AssertionMismatch`` hint + ``rule_invariant_violation`` — AC-769 wiring
     for runtime-side invariant failures (post-flight).
"""

from __future__ import annotations

import textwrap
from dataclasses import dataclass, field
from pathlib import Path

import pytest

from autocontext.harness.evaluation.failure_report import FailureReport, MatchDiagnosis
from autocontext.loop.spec_verifier import (
    AssertionMismatch,
    Invariant,
    InvariantCache,
    InvariantSet,
    derive_invariants,
    parse_invariants,
    render_invariants,
    rule_invariant_violation,
    spec_hash,
)
from autocontext.providers.base import CompletionResult, LLMProvider

# --------------------------------------------------------------------------
# Stub provider — returns canned responses for tests.
# --------------------------------------------------------------------------


@dataclass(slots=True)
class StubProvider(LLMProvider):
    responses: list[str] = field(default_factory=list)
    calls: list[tuple[str, str]] = field(default_factory=list)

    def complete(
        self,
        system_prompt: str,
        user_prompt: str,
        model: str | None = None,
        temperature: float = 0.0,
        max_tokens: int = 4096,
    ) -> CompletionResult:
        self.calls.append((system_prompt, user_prompt))
        if not self.responses:
            raise RuntimeError("stub has no more canned responses")
        return CompletionResult(text=self.responses.pop(0), model="stub")

    def default_model(self) -> str:
        return "stub"


# --------------------------------------------------------------------------
# 1. parse_invariants
# --------------------------------------------------------------------------


class TestParseInvariants:
    def test_single_invariant(self) -> None:
        text = textwrap.dedent("""\
            <invariant kind="roundtrip" confidence="high">
            decrypt(encrypt(x, key), key) == x for all x
            </invariant>
        """)
        invs = parse_invariants(text)
        assert len(invs) == 1
        assert invs[0].kind == "roundtrip"
        assert invs[0].confidence == "high"
        assert "decrypt(encrypt" in invs[0].statement

    def test_multiple_invariants(self) -> None:
        text = textwrap.dedent("""\
            Some preamble the LLM emitted before the block.

            <invariant kind="literal" confidence="high">
            The recovered first line equals "I have met them at close of day".
            </invariant>

            <invariant kind="structural" confidence="medium">
            The output is exactly 40 lines.
            </invariant>

            Trailing commentary.
        """)
        invs = parse_invariants(text)
        assert len(invs) == 2
        assert {i.kind for i in invs} == {"literal", "structural"}

    def test_unknown_kind_falls_back_to_metric(self) -> None:
        text = '<invariant kind="wild" confidence="high">stmt</invariant>'
        invs = parse_invariants(text)
        assert len(invs) == 1
        assert invs[0].kind == "metric"

    def test_missing_confidence_defaults_to_medium(self) -> None:
        text = '<invariant kind="roundtrip">stmt</invariant>'
        invs = parse_invariants(text)
        assert invs[0].confidence == "medium"

    def test_no_invariants_returns_empty(self) -> None:
        assert parse_invariants("the LLM forgot to emit any") == []

    def test_malformed_block_skipped(self) -> None:
        text = '<invariant kind="roundtrip" confidence="high">missing close'
        assert parse_invariants(text) == []

    def test_strips_whitespace_in_statement(self) -> None:
        text = textwrap.dedent("""\
            <invariant kind="roundtrip" confidence="high">
                multi
                line
                statement
            </invariant>
        """)
        invs = parse_invariants(text)
        assert "multi" in invs[0].statement
        assert invs[0].statement.endswith("statement")


# --------------------------------------------------------------------------
# 2. render_invariants
# --------------------------------------------------------------------------


class TestRenderInvariants:
    def test_empty_set_renders_empty_string(self) -> None:
        empty = InvariantSet(invariants=[], spec_hash="x")
        assert render_invariants(empty) == ""

    def test_renders_section_header_and_bullets(self) -> None:
        s = InvariantSet(
            invariants=[
                Invariant(
                    kind="roundtrip",
                    statement="decrypt(encrypt(x)) == x",
                    confidence="high",
                ),
                Invariant(
                    kind="literal",
                    statement='Output starts with "I have met them"',
                    confidence="medium",
                ),
            ],
            spec_hash="abc123",
        )
        block = render_invariants(s)
        assert "## Solution invariants" in block
        assert "decrypt(encrypt(x)) == x" in block
        assert "Output starts with" in block
        # confidence is surfaced compactly (e.g. tag or annotation)
        assert "high" in block.lower() or "medium" in block.lower()

    def test_kinds_distinguishable_in_output(self) -> None:
        s = InvariantSet(
            invariants=[
                Invariant(kind="structural", statement="40 lines", confidence="high"),
            ],
            spec_hash="x",
        )
        block = render_invariants(s)
        assert "structural" in block.lower()


# --------------------------------------------------------------------------
# 3. InvariantCache
# --------------------------------------------------------------------------


class TestInvariantCache:
    def test_put_then_get_roundtrip(self, tmp_path: Path) -> None:
        cache = InvariantCache(tmp_path)
        s = InvariantSet(
            invariants=[Invariant(kind="roundtrip", statement="x==y", confidence="high")],
            spec_hash="h1",
        )
        cache.put("scen", s)
        loaded = cache.get("scen", "h1")
        assert loaded is not None
        assert loaded.spec_hash == "h1"
        assert loaded.invariants[0].statement == "x==y"

    def test_get_missing_returns_none(self, tmp_path: Path) -> None:
        assert InvariantCache(tmp_path).get("scen", "no_such_hash") is None

    def test_get_with_stale_hash_returns_none(self, tmp_path: Path) -> None:
        cache = InvariantCache(tmp_path)
        s = InvariantSet(invariants=[], spec_hash="old_hash")
        cache.put("scen", s)
        assert cache.get("scen", "new_hash") is None

    def test_unsafe_scenario_name_rejected(self, tmp_path: Path) -> None:
        """Same safe-name discipline as AC-767's FixtureCache."""
        cache = InvariantCache(tmp_path)
        s = InvariantSet(invariants=[], spec_hash="h")
        with pytest.raises(ValueError):
            cache.put("../escape", s)


# --------------------------------------------------------------------------
# 4. derive_invariants (end-to-end orchestration)
# --------------------------------------------------------------------------


class TestDeriveInvariants:
    def test_calls_provider_with_spec_and_parses_response(self, tmp_path: Path) -> None:
        canned = textwrap.dedent("""\
            <invariant kind="roundtrip" confidence="high">
            decrypt(encrypt(x, key), key) == x
            </invariant>
        """)
        provider = StubProvider(responses=[canned])
        cache = InvariantCache(tmp_path)
        spec = "Implement AES-CBC encrypt/decrypt. Roundtrip must hold."

        result = derive_invariants(spec, scenario="scen", provider=provider, cache=cache)
        assert len(result.invariants) == 1
        assert result.invariants[0].kind == "roundtrip"
        # One call to provider.
        assert len(provider.calls) == 1

    def test_caches_result_on_first_call(self, tmp_path: Path) -> None:
        provider = StubProvider(responses=['<invariant kind="metric" confidence="medium">stmt</invariant>'])
        cache = InvariantCache(tmp_path)
        spec = "task spec"

        first = derive_invariants(spec, scenario="scen", provider=provider, cache=cache)
        # Second call with same spec must hit cache, no provider call.
        second = derive_invariants(spec, scenario="scen", provider=provider, cache=cache)

        assert first.spec_hash == second.spec_hash
        assert len(provider.calls) == 1  # cached on the second call

    def test_spec_change_invalidates_cache(self, tmp_path: Path) -> None:
        provider = StubProvider(
            responses=[
                '<invariant kind="metric" confidence="medium">a</invariant>',
                '<invariant kind="metric" confidence="medium">b</invariant>',
            ]
        )
        cache = InvariantCache(tmp_path)
        first = derive_invariants("spec v1", scenario="scen", provider=provider, cache=cache)
        second = derive_invariants("spec v2", scenario="scen", provider=provider, cache=cache)
        assert first.spec_hash != second.spec_hash
        assert len(provider.calls) == 2

    def test_provider_returning_no_invariants_yields_empty_set(self, tmp_path: Path) -> None:
        provider = StubProvider(responses=["no invariants here"])
        cache = InvariantCache(tmp_path)
        result = derive_invariants("spec", scenario="scen", provider=provider, cache=cache)
        assert result.invariants == []

    def test_fixtures_referenced_in_prompt(self, tmp_path: Path) -> None:
        """When fixtures are passed, the LLM prompt should include them as
        ground-truth references the invariants can reason about."""
        provider = StubProvider(responses=['<invariant kind="literal" confidence="high">stmt</invariant>'])
        cache = InvariantCache(tmp_path)
        fixtures_block = "## Available fixtures\n- `expected_first_line`: 'I have met them'"
        derive_invariants(
            "spec",
            scenario="scen",
            provider=provider,
            cache=cache,
            fixtures_block=fixtures_block,
        )
        # The user prompt sent to the provider should mention the fixture text.
        _, user_prompt = provider.calls[0]
        assert "expected_first_line" in user_prompt


def spec_hash_(text: str) -> str:
    # Local helper, distinct from the import to keep test concerns isolated.
    return spec_hash(text)


class TestSpecHash:
    def test_deterministic(self) -> None:
        assert spec_hash("hello") == spec_hash("hello")

    def test_different_inputs_different_hashes(self) -> None:
        assert spec_hash("a") != spec_hash("b")

    def test_returns_short_hex(self) -> None:
        h = spec_hash("anything")
        assert isinstance(h, str)
        assert len(h) >= 8
        # All hex
        int(h, 16)


# --------------------------------------------------------------------------
# 5. AssertionMismatch hint + rule_invariant_violation (AC-769 wiring)
# --------------------------------------------------------------------------


class TestRuleInvariantViolation:
    def test_invariant_violation_in_errors_emits_assertion_mismatch(self) -> None:
        report = FailureReport(
            match_diagnoses=[
                MatchDiagnosis(
                    match_index=0,
                    score=0.0,
                    passed=False,
                    errors=[
                        'InvariantViolation: roundtrip "decrypt(encrypt(x)) == x" failed; observed: decrypt returned b"garbage"'
                    ],
                    summary="match",
                )
            ],
            overall_delta=0.0,
            threshold=0.0,
            previous_best=0.0,
            current_best=0.0,
            strategy_summary="",
        )
        hints = rule_invariant_violation(report)
        assert len(hints) == 1
        assert isinstance(hints[0], AssertionMismatch)
        assert "roundtrip" in hints[0].invariant

    def test_no_violation_yields_no_hint(self) -> None:
        report = FailureReport(
            match_diagnoses=[
                MatchDiagnosis(
                    match_index=0,
                    score=0.0,
                    passed=False,
                    errors=["AssertionError: unrelated failure"],
                    summary="match",
                )
            ],
            overall_delta=0.0,
            threshold=0.0,
            previous_best=0.0,
            current_best=0.0,
            strategy_summary="",
        )
        assert rule_invariant_violation(report) == []

    def test_multiple_violations_emit_one_hint_each(self) -> None:
        report = FailureReport(
            match_diagnoses=[
                MatchDiagnosis(
                    match_index=0,
                    score=0.0,
                    passed=False,
                    errors=[
                        'InvariantViolation: structural "40 lines" failed; observed: 39',
                        'InvariantViolation: literal "starts with I" failed; observed: "Z..."',
                    ],
                    summary="match",
                )
            ],
            overall_delta=0.0,
            threshold=0.0,
            previous_best=0.0,
            current_best=0.0,
            strategy_summary="",
        )
        hints = rule_invariant_violation(report)
        assert len(hints) == 2

    def test_hint_renders_in_router_block(self) -> None:
        """Render the new hint type via the existing render_hints function."""
        from autocontext.loop.remediation_router import render_hints

        hint = AssertionMismatch(invariant="roundtrip x==y", observed="b'garbage'", reason="match 0")
        out = render_hints([hint])
        assert "roundtrip" in out
        assert "garbage" in out


# --------------------------------------------------------------------------
# Cross-concern integration: end-to-end pre-flight wiring shape.
# --------------------------------------------------------------------------


class TestPreflightWiring:
    def test_derived_set_is_rendered_into_prompt_block(self, tmp_path: Path) -> None:
        provider = StubProvider(
            responses=[
                textwrap.dedent("""\
                    <invariant kind="roundtrip" confidence="high">
                    decrypt(encrypt(x, key), key) == x for all x
                    </invariant>
                """)
            ]
        )
        cache = InvariantCache(tmp_path)
        spec = "Implement AES-CBC roundtrip."

        s = derive_invariants(spec, scenario="scen", provider=provider, cache=cache)
        block = render_invariants(s)
        # The full pre-flight chain produces a non-empty prompt block.
        assert block
        assert "## Solution invariants" in block
        assert "decrypt(encrypt(x, key), key)" in block
