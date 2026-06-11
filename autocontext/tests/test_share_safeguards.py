"""Parity tests for the Python tier-1 safeguards port.

The toxic-fixture corpus is vendored byte-identical from autocontext-website
(tests/fixtures/trace-exchange) and is the cross-consumer contract: the Python
scanner must produce the same findings, verdicts, and redaction manifests as the
TypeScript source of truth.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from autocontext.sharing.review_state import (
    REVIEW_STATE_META,
    REVIEW_TRANSITIONS,
    can_transition_review,
    get_next_review_states,
)
from autocontext.sharing.safeguards import (
    ReviewState,
    apply_redactions,
    combine_review_verdicts,
    extract_references,
    get_scan_verdict,
    is_luhn_valid,
    scan_content,
    shannon_entropy_bits_per_char,
)

FIXTURES = Path(__file__).parent / "fixtures" / "trace_exchange"


def _read(rel: str) -> str:
    return (FIXTURES / rel).read_text(encoding="utf-8")


TOXIC_CASES = [
    pytest.param(
        "toxic/secrets.env.txt",
        None,
        [
            "aws-access-key",
            "provider-api-key",
            "github-token",
            "slack-token",
            "google-api-key",
            "doppler-token",
            "npm-token",
            "pypi-token",
            "database-dsn",
            "jwt",
            "credential-assignment",
            "env-assignment",
            "gcp-service-account",
        ],
        "needs_user_redaction",
        id="secrets",
    ),
    pytest.param(
        "toxic/pii.report.md",
        "report",
        ["email-address", "phone-number", "payment-card", "home-path", "ipv4-address"],
        "needs_user_redaction",
        id="pii",
    ),
    pytest.param(
        "toxic/encoded.trace.jsonl",
        "trace",
        ["base64-span", "data-url", "pem-block", "gzip-base64", "hex-blob", "invalid-jsonl-line"],
        "rejected",
        id="encoded",
    ),
    pytest.param(
        "toxic/malicious.tool.py",
        "tool",
        [
            "pipe-to-shell",
            "destructive-rm",
            "reverse-shell",
            "eval-decode",
            "powershell-encoded",
            "credential-file-access",
            "prompt-injection",
        ],
        "rejected",
        id="malicious",
    ),
]


class TestToxicCorpus:
    @pytest.mark.parametrize("rel, kind, expected_rules, expected_verdict", TOXIC_CASES)
    def test_recall_and_verdict(
        self, rel: str, kind: str | None, expected_rules: list[str], expected_verdict: ReviewState
    ) -> None:
        report = scan_content(_read(rel), kind=kind)
        found = {finding.rule_id for finding in report.findings}
        for rule in expected_rules:
            assert rule in found, f"expected rule {rule} in {rel}"
        assert report.verdict == expected_verdict

    def test_only_luhn_valid_card_flagged(self) -> None:
        report = scan_content(_read("toxic/pii.report.md"), kind="report")
        cards = [f for f in report.findings if f.rule_id == "payment-card"]
        assert len(cards) == 1

    def test_reviewer_evidence_references(self) -> None:
        refs = extract_references(_read("toxic/malicious.tool.py"))
        assert "https://tools.example/install.sh" in refs.urls
        assert "os" in refs.dependencies
        assert "requests" in refs.dependencies

    def test_jsonl_validation_is_kind_scoped(self) -> None:
        text = _read("toxic/encoded.trace.jsonl")
        as_trace = scan_content(text, kind="trace")
        as_report = scan_content(text, kind="report")
        assert any(f.rule_id == "invalid-jsonl-line" for f in as_trace.findings)
        assert not any(f.rule_id == "invalid-jsonl-line" for f in as_report.findings)


class TestControlCorpus:
    @pytest.mark.parametrize(
        "rel, kind",
        [("clean/report.md", "report"), ("clean/playbook.md", "playbook"), ("clean/trace.jsonl", "trace")],
    )
    def test_zero_findings(self, rel: str, kind: str) -> None:
        report = scan_content(_read(rel), kind=kind)
        assert report.findings == []
        assert report.verdict == "needs_human_review"
        assert report.redaction_manifest == []


class TestRedaction:
    def test_placeholder_scheme_and_manifest(self) -> None:
        text = "email j.alvarez@northwind.example and key AKIAIOSFODNN7EXAMPLE"
        report = scan_content(text)
        redacted, manifest = apply_redactions(text, report.findings)
        assert "[REDACTED:email-address]" in redacted
        assert "[REDACTED:aws-access-key]" in redacted
        assert "AKIAIOSFODNN7EXAMPLE" not in redacted
        assert "j.alvarez@northwind.example" not in redacted
        email = next(e for e in manifest if e.rule_id == "email-address")
        assert email.count == 1

    def test_excerpts_are_masked(self) -> None:
        report = scan_content("key=AKIAIOSFODNN7EXAMPLE")
        finding = next(f for f in report.findings if f.rule_id == "aws-access-key")
        assert finding.excerpt != "AKIAIOSFODNN7EXAMPLE"
        assert "…" in finding.excerpt


class TestLuhn:
    def test_valid(self) -> None:
        assert is_luhn_valid("4111 1111 1111 1111")
        assert is_luhn_valid("4111111111111111")

    def test_invalid(self) -> None:
        assert not is_luhn_valid("1234 5678 9012 3456")
        assert not is_luhn_valid("4111")
        assert not is_luhn_valid("4" * 23)


class TestEntropy:
    def test_repeated_token_is_zero(self) -> None:
        assert shannon_entropy_bits_per_char("a" * 16) == 0.0

    def test_random_token_above_threshold(self) -> None:
        assert shannon_entropy_bits_per_char("q7Zp2VxKfM9rTb4Wc8sYdH3gJn6LmEuA0iO5kP1v") > 4.6


class TestPerKindProfiles:
    DANGEROUS = "incident note: the attacker ran rm -rf / on the host"

    def test_narrative_downgrades_malicious_reject(self) -> None:
        report = scan_content(self.DANGEROUS, kind="report")
        finding = next(f for f in report.findings if f.rule_id == "destructive-rm")
        assert finding.severity == "review"
        assert report.verdict == "needs_human_review"

    def test_tool_keeps_malicious_reject(self) -> None:
        report = scan_content(self.DANGEROUS, kind="tool")
        finding = next(f for f in report.findings if f.rule_id == "destructive-rm")
        assert finding.severity == "reject"
        assert report.verdict == "rejected"

    def test_secrets_never_downgraded(self) -> None:
        report = scan_content("key AKIAIOSFODNN7EXAMPLE here", kind="report")
        finding = next(f for f in report.findings if f.rule_id == "aws-access-key")
        assert finding.severity == "redact"


class TestExpandedProviders:
    def test_groq_stripe_bearer(self) -> None:
        report = scan_content(
            "\n".join(
                [
                    "groq=gsk_abcdefghijklmnopqrstuvwx",
                    "stripe=sk_live_abcdefghijklmnopqrst",
                    "Authorization: Bearer abcdefghijklmnopqrstuvwx",
                ]
            )
        )
        found = {f.rule_id for f in report.findings}
        assert {"groq-key", "stripe-key", "bearer-auth"} <= found
        assert report.verdict == "needs_user_redaction"


class TestVerdictCombinator:
    def test_clean_still_needs_review(self) -> None:
        assert get_scan_verdict([]) == "needs_human_review"

    @pytest.mark.parametrize("model", ["clear", "needs_user_redaction", "needs_human_review", "reject"])
    def test_never_softens_rejection(self, model: str) -> None:
        assert combine_review_verdicts("rejected", model) == "rejected"

    def test_model_clear_no_privilege(self) -> None:
        assert combine_review_verdicts("needs_human_review", "clear") == "needs_human_review"
        assert combine_review_verdicts("needs_user_redaction", "clear") == "needs_user_redaction"

    def test_model_only_downgrades(self) -> None:
        assert combine_review_verdicts("needs_human_review", "reject") == "rejected"
        assert combine_review_verdicts("needs_human_review", "needs_user_redaction") == "needs_user_redaction"


def _all_simple_paths(start: ReviewState, goal: ReviewState) -> list[list[ReviewState]]:
    paths: list[list[ReviewState]] = []
    stack: list[list[ReviewState]] = [[start]]
    while stack:
        path = stack.pop()
        current = path[-1]
        if current == goal:
            paths.append(path)
            continue
        for nxt in REVIEW_TRANSITIONS[current]:
            if nxt not in path:
                stack.append([*path, nxt])
    return paths


class TestReviewStateMachine:
    def test_initial_only_quarantines(self) -> None:
        assert get_next_review_states("uploaded") == ["quarantined"]

    def test_no_path_to_public_skips_review(self) -> None:
        paths = _all_simple_paths("uploaded", "approved_public")
        assert paths
        for path in paths:
            assert "needs_human_review" in path
            assert "quarantined" in path
            assert "scanning" in path

    def test_private_also_requires_review(self) -> None:
        paths = _all_simple_paths("uploaded", "approved_private")
        assert paths
        assert all("needs_human_review" in path for path in paths)

    def test_terminal_states(self) -> None:
        for state, meta in REVIEW_STATE_META.items():
            if meta.terminal:
                assert REVIEW_TRANSITIONS[state] == []

    def test_promotion_needs_fresh_review(self) -> None:
        assert not can_transition_review("approved_private", "approved_public")
        assert can_transition_review("approved_private", "needs_human_review")


class TestUnicodeWhitespaceParity:
    """JS \\s/\\S are Unicode-aware; the port must not fail-open on NBSP etc."""

    def test_malicious_reject_through_nbsp(self) -> None:
        # non-breaking spaces between tokens must still reject (no evasion)
        nbsp = "\u00a0"
        report = scan_content(f"rm{nbsp}-rf{nbsp}/", kind="tool")
        assert any(f.rule_id == "destructive-rm" for f in report.findings)
        assert report.verdict == "rejected"

    def test_secret_redact_through_unicode_space(self) -> None:
        report = scan_content("Authorization: Bearer\u00a0abcdefghijklmnopqrstuvwx")
        assert any(f.rule_id == "bearer-auth" for f in report.findings)

    def test_ascii_word_boundaries_stay_ascii(self) -> None:
        # \\b/\\d/\\w remain ASCII (re.ASCII) — an exotic-digit run is not a card
        report = scan_content("４１１１ 1111 1111 1111", kind="report")
        assert not any(f.rule_id == "payment-card" for f in report.findings)


class TestJsonlNonFinite:
    def test_bare_nan_is_invalid_like_js(self) -> None:
        report = scan_content('{"loss": NaN}\n{"ok": 1}', kind="dataset")
        assert any(f.rule_id == "invalid-jsonl-line" for f in report.findings)
