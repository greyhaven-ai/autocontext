"""AC-706: Hermes redaction policy module.

Covers:
* mode validation (off / standard / strict),
* default ``standard`` mode redacts API keys, bearer tokens, emails,
  IPs, env values, absolute paths, and high-risk file references,
* ``strict`` mode requires at least one user pattern,
* ``strict`` mode redacts user-defined regexes after the built-in
  pipeline and tags hits with ``[REDACTED_USER_PATTERN:<name>]``,
* ``compile_user_patterns`` rejects empty names / patterns and
  uncompilable regexes,
* ``RedactionStats`` accumulates per-category counts.
"""

from __future__ import annotations

import pytest

from autocontext.hermes.redaction import (
    RedactionPolicy,
    RedactionStats,
    UserPattern,
    compile_user_patterns,
    redact_text,
)


def test_off_mode_passes_text_through_unchanged() -> None:
    raw = "Authorization: Bearer sk-ant-abcdef1234567890abcdef"
    out, stats = redact_text(raw, RedactionPolicy(mode="off"))
    assert out == raw
    assert stats.total == 0


def test_standard_mode_redacts_api_keys_and_emails() -> None:
    raw = "key=sk-ant-abcdef1234567890abcdef alice@example.com"
    out, stats = redact_text(raw, RedactionPolicy(mode="standard"))
    assert "sk-ant-" not in out
    assert "alice@example.com" not in out
    assert stats.total >= 2
    # The category names match autocontext.sharing.redactor; api_key:<name>.
    assert any(k.startswith("api_key:anthropic") for k in stats.by_category)
    assert "email" in stats.by_category


def test_standard_mode_redacts_bearer_tokens_and_absolute_paths() -> None:
    raw = "Authorization: Bearer abc123 file /Users/alice/.ssh/id_rsa here"
    out, _ = redact_text(raw, RedactionPolicy(mode="standard"))
    assert "Bearer abc123" not in out
    # `/Users/alice/.ssh/id_rsa` is a high-risk reference; the redactor
    # rewrites the whole line to the high-risk marker so the path itself
    # is gone too.
    assert "/Users/alice" not in out
    assert "/.ssh/id_rsa" not in out


def test_strict_mode_requires_user_patterns() -> None:
    with pytest.raises(ValueError, match="strict.*requires.*user pattern"):
        RedactionPolicy(mode="strict", user_patterns=())


def test_strict_mode_redacts_user_pattern_after_built_in_pipeline() -> None:
    import re

    user = UserPattern(name="ticket", pattern=re.compile(r"TKT-\d+"))
    policy = RedactionPolicy(mode="strict", user_patterns=(user,))
    raw = "Ticket TKT-12345 references alice@example.com"
    out, stats = redact_text(raw, policy)
    assert "TKT-12345" not in out
    assert "[REDACTED_USER_PATTERN:ticket]" in out
    assert "alice@example.com" not in out
    assert stats.by_category["user_pattern:ticket"] == 1
    assert "email" in stats.by_category


def test_unknown_mode_raises() -> None:
    with pytest.raises(ValueError, match="unknown redaction mode"):
        RedactionPolicy(mode="nuke-everything")


def test_compile_user_patterns_accepts_well_formed_list() -> None:
    raw = [
        {"name": "ticket", "pattern": r"TKT-\d+"},
        {"name": "case", "pattern": r"CASE-[A-Z]{3}"},
    ]
    patterns = compile_user_patterns(raw)
    assert len(patterns) == 2
    assert patterns[0].name == "ticket"
    assert patterns[1].pattern.match("CASE-ABC") is not None


def test_compile_user_patterns_rejects_missing_name() -> None:
    with pytest.raises(ValueError, match="missing or empty 'name'"):
        compile_user_patterns([{"name": "", "pattern": "x"}])


def test_compile_user_patterns_rejects_missing_pattern() -> None:
    with pytest.raises(ValueError, match="missing or empty 'pattern'"):
        compile_user_patterns([{"name": "ok", "pattern": ""}])


def test_compile_user_patterns_rejects_bad_regex() -> None:
    with pytest.raises(ValueError, match="not a valid regex"):
        compile_user_patterns([{"name": "bad", "pattern": "([unclosed"}])


def test_compile_user_patterns_handles_none_input() -> None:
    assert compile_user_patterns(None) == ()
    assert compile_user_patterns([]) == ()


def test_redaction_stats_accumulates_by_category() -> None:
    stats = RedactionStats()
    stats.add("email", 2)
    stats.add("email")
    stats.add("api_key:anthropic")
    assert stats.total == 4
    assert stats.by_category == {"email": 3, "api_key:anthropic": 1}


def test_empty_string_returns_zero_stats() -> None:
    out, stats = redact_text("", RedactionPolicy(mode="standard"))
    assert out == ""
    assert stats.total == 0
