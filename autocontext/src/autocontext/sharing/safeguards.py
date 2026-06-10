"""Tier-1 deterministic safeguards for the trace exchange (share prepare).

This is the Python consumer of the shared detector rule set defined in the
website source of truth
(``src/features/context-hub/safeguards/`` in autocontext-website). It is a
faithful, fail-closed transcription kept in parity with that TypeScript module
and pinned by ``RULESET_VERSION``; the toxic-fixture corpus is the contract
test across both consumers.

Severity is fail-closed: ``redact`` spans can be scrubbed with a manifest,
``review`` requires a human, ``reject`` blocks the bundle outright. Even a
clean scan resolves to ``needs_human_review`` — nothing here auto-approves.

Patterns are compiled with ``re.ASCII`` so ``\\b``/``\\d``/``\\w`` match the
ASCII semantics of the JavaScript ``RegExp`` source.
"""

from __future__ import annotations

import json
import math
import re
from dataclasses import dataclass, field
from typing import Literal

RULESET_VERSION = "trace-exchange-rules.v1"

ScannerId = Literal["secrets", "pii", "encoded", "malicious"]
ScanSeverity = Literal["redact", "review", "reject"]
ScannerResult = Literal["passed", "flagged"]
ReviewState = Literal[
    "uploaded",
    "quarantined",
    "scanning",
    "needs_user_redaction",
    "needs_human_review",
    "approved_private",
    "approved_public",
    "rejected",
    "takedown",
]
BundleFileKind = Literal["playbook", "hints", "lessons", "tool", "trace", "report", "dataset"]

SCAN_EXCERPT_VISIBLE_CHARS = 14
ENTROPY_MIN_TOKEN_LENGTH = 40
ENTROPY_THRESHOLD_BITS = 4.6
REDACTION_PREFIX = "[REDACTED:"
REDACTION_SUFFIX = "]"

# Narrative kinds where malicious-code *patterns* are usually inert quoted text
# (a postmortem citing ``rm -rf``). Their reject is downgraded to review;
# coverage is never lost, only the auto-reject.
NARRATIVE_KINDS: frozenset[str] = frozenset({"report", "playbook", "hints", "lessons"})
_JSONL_KINDS: frozenset[str] = frozenset({"trace", "dataset"})


@dataclass(slots=True, frozen=True)
class ScanRule:
    id: str
    scanner: ScannerId
    label: str
    severity: ScanSeverity
    pattern: re.Pattern[str]


# JavaScript ``RegExp`` ``\s``/``\S`` are Unicode-aware regardless of flags, but
# Python's ``re.ASCII`` — which we want so ``\b``/``\d``/``\w`` match JS's ASCII
# defaults — also narrows ``\s``/``\S`` to ASCII. To match JS exactly we keep
# ``re.ASCII`` for ``\b``/``\d``/``\w`` and translate every standalone ``\s``/``\S``
# to an explicit class spanning the ECMAScript WhiteSpace + LineTerminator set.
# Without this, Unicode-whitespace-separated tokens (e.g. ``rm -rf``) would
# evade reject-severity malicious rules that the TypeScript source of truth
# catches — a fail-open the toxic fixtures (plain ASCII) do not exercise.
_WS_CLASS_BODY = r" \t\n\r\f\v\u00a0\u1680\u2000-\u200a\u2028\u2029\u202f\u205f\u3000\ufeff"
_WS = f"[{_WS_CLASS_BODY}]"
_NON_WS = f"[^{_WS_CLASS_BODY}]"


def _unicode_ws(pattern: str) -> str:
    """Translate standalone ``\\s``/``\\S`` to JS-equivalent Unicode whitespace.

    In-class uses (e.g. ``[^\\s@/]``) must inline ``_WS_CLASS_BODY`` directly
    instead of relying on this helper, which only handles standalone tokens.
    """
    return pattern.replace(r"\S", _NON_WS).replace(r"\s", _WS)


def _rule(
    rule_id: str,
    scanner: ScannerId,
    label: str,
    severity: ScanSeverity,
    pattern: str,
    flags: int = 0,
) -> ScanRule:
    return ScanRule(rule_id, scanner, label, severity, re.compile(_unicode_ws(pattern), re.ASCII | flags))


# Order mirrors the TypeScript source exactly so identical-index tie-breaking
# during redaction overlap-collapse matches across consumers.
SCAN_RULES: list[ScanRule] = [
    _rule("aws-access-key", "secrets", "AWS access key id", "redact", r"\bAKIA[0-9A-Z]{16}\b"),
    _rule("provider-api-key", "secrets", "model-provider API key", "redact", r"\bsk-[A-Za-z0-9_-]{20,}\b"),
    _rule("github-token", "secrets", "GitHub token", "redact", r"\bgh[pousr]_[A-Za-z0-9]{30,}\b"),
    _rule("slack-token", "secrets", "Slack token", "redact", r"\bxox[abprs]-[A-Za-z0-9-]{10,}\b"),
    _rule("google-api-key", "secrets", "Google API key", "redact", r"\bAIza[0-9A-Za-z_-]{35}\b"),
    _rule("doppler-token", "secrets", "Doppler token", "redact", r"\bdp\.(?:st|pt|ct)\.[A-Za-z0-9._-]{20,}\b"),
    _rule("npm-token", "secrets", "npm token", "redact", r"\bnpm_[A-Za-z0-9]{30,}\b"),
    _rule("pypi-token", "secrets", "PyPI token", "redact", r"\bpypi-AgEI[A-Za-z0-9_-]{20,}\b"),
    _rule(
        "database-dsn",
        "secrets",
        "database DSN with credentials",
        "redact",
        # \s inlined as _WS_CLASS_BODY here because _unicode_ws only rewrites
        # standalone \s/\S, not in-class uses like [^\s@/].
        rf"\b(?:postgres(?:ql)?|mysql|mongodb(?:\+srv)?|redis|amqp)://[^{_WS_CLASS_BODY}@/]+@[^{_WS_CLASS_BODY}\"']+",
    ),
    _rule("gcp-service-account", "secrets", "GCP service-account JSON", "review", r'"type":\s*"service_account"'),
    _rule(
        "jwt",
        "secrets",
        "JSON Web Token",
        "redact",
        r"\beyJ[A-Za-z0-9_-]{10,}\.[A-Za-z0-9_-]{10,}\.[A-Za-z0-9_-]{10,}\b",
    ),
    _rule(
        "credential-assignment",
        "secrets",
        "credential assignment",
        "redact",
        r"\b(?:api[_-]?key|apikey|secret|token|passwd|password)[\"']?\s*[:=]\s*[\"']?[A-Za-z0-9_/+.-]{12,}",
        re.IGNORECASE,
    ),
    _rule("env-assignment", "secrets", "environment variable line", "review", r"^[A-Z][A-Z0-9_]{2,}=\S{6,}$", re.MULTILINE),
    _rule("groq-key", "secrets", "Groq API key", "redact", r"\bgsk_[A-Za-z0-9]{20,}\b"),
    _rule("stripe-key", "secrets", "Stripe secret key", "redact", r"\b[sr]k_(?:live|test)_[A-Za-z0-9]{16,}\b"),
    _rule("bearer-auth", "secrets", "bearer authorization token", "redact", r"\bBearer\s+[A-Za-z0-9._~+/-]{20,}=*\b"),
    _rule("email-address", "pii", "email address", "redact", r"\b[A-Za-z0-9._%+-]+@[A-Za-z0-9.-]+\.[A-Za-z]{2,}\b"),
    _rule(
        "phone-number",
        "pii",
        "phone number",
        "redact",
        r"\b(?:\+?\d{1,3}[ .-])?\(?\d{3}\)?[ .-]\d{3}[ .-]?\d{4}\b",
    ),
    _rule("payment-card", "pii", "payment card number", "redact", r"\b\d{4}[ -]\d{4}[ -]\d{4}[ -]\d{1,4}\b"),
    _rule("home-path", "pii", "user home path", "redact", r"(?:/Users|/home)/[A-Za-z0-9._-]+"),
    _rule("ipv4-address", "pii", "IPv4 address", "review", r"\b\d{1,3}(?:\.\d{1,3}){3}\b"),
    _rule("base64-span", "encoded", "long base64 span", "reject", r"\b[A-Za-z0-9+/]{80,}={0,2}\b"),
    _rule(
        "data-url",
        "encoded",
        "base64 data URL",
        "reject",
        r"\bdata:[\w.+-]+/[\w.+-]+;base64,[A-Za-z0-9+/=]{16,}",
    ),
    _rule("pem-block", "encoded", "PEM block", "reject", r"-{5}BEGIN [A-Z ]+-{5}"),
    _rule("gzip-base64", "encoded", "gzip magic bytes (base64)", "reject", r"\bH4sI[A-Za-z0-9+/=]{12,}"),
    _rule("hex-blob", "encoded", "long hex blob", "reject", r"\b[0-9a-fA-F]{96,}\b"),
    _rule(
        "pipe-to-shell",
        "malicious",
        "download piped to shell",
        "reject",
        r"\b(?:curl|wget)\b[^\n|]{0,160}\|\s*(?:ba|z|da)?sh\b",
    ),
    _rule("destructive-rm", "malicious", "destructive filesystem command", "reject", r"\brm\s+-rf?\s+[~/.]"),
    _rule(
        "reverse-shell",
        "malicious",
        "reverse shell pattern",
        "reject",
        r"\bnc\s+(?:-e|--exec)\b|/dev/tcp/|\bbash\s+-i\s+>&",
    ),
    _rule(
        "eval-decode",
        "malicious",
        "eval over decoded payload",
        "reject",
        r"\b(?:eval|exec)\s*\(\s*(?:atob|base64|compile|codecs)",
        re.IGNORECASE,
    ),
    _rule(
        "powershell-encoded",
        "malicious",
        "encoded PowerShell command",
        "reject",
        r"powershell[^\n]{0,80}-enc(?:odedcommand)?\b",
        re.IGNORECASE,
    ),
    _rule(
        "credential-file-access",
        "malicious",
        "credential file reference",
        "review",
        r"\.aws/credentials|\.ssh/id_[a-z0-9]+|\.netrc\b",
    ),
    _rule(
        "prompt-injection",
        "malicious",
        "prompt-injection phrase",
        "review",
        r"ignore (?:all )?(?:previous|prior|above) instructions"
        r"|disregard (?:all )?(?:previous|prior) (?:instructions|rules)"
        r"|reveal (?:your|the) system prompt",
        re.IGNORECASE,
    ),
]

_HIGH_ENTROPY_RULE_ID = "high-entropy-span"
_INVALID_JSONL_RULE_ID = "invalid-jsonl-line"
_ENTROPY_TOKEN_PATTERN = re.compile(_unicode_ws(r"\S{40,}"), re.ASCII)
_URL_REFERENCE_PATTERN = re.compile(rf"\bhttps?://[^{_WS_CLASS_BODY}\"'<>)\]]+", re.ASCII)
_PYTHON_IMPORT_PATTERN = re.compile(_unicode_ws(r"^\s*(?:from|import)\s+([A-Za-z_][\w.]*)"), re.ASCII | re.MULTILINE)
_JS_IMPORT_PATTERN = re.compile(_unicode_ws(r"(?:\bfrom\s+|\brequire\s*\(\s*)[\"']([^\"']+)[\"']"), re.ASCII)


@dataclass(slots=True)
class ScanFinding:
    rule_id: str
    scanner: ScannerId
    label: str
    severity: ScanSeverity
    excerpt: str
    index: int
    length: int


@dataclass(slots=True)
class RedactionEntry:
    rule_id: str
    label: str
    count: int


@dataclass(slots=True)
class ScanReferences:
    urls: list[str] = field(default_factory=list)
    dependencies: list[str] = field(default_factory=list)


@dataclass(slots=True)
class ScanReport:
    findings: list[ScanFinding]
    verdict: ReviewState
    redacted_text: str
    redaction_manifest: list[RedactionEntry]
    scanner_results: dict[ScannerId, ScannerResult]
    references: ScanReferences
    ruleset_version: str = RULESET_VERSION


def scan_content(text: str, kind: BundleFileKind | None = None) -> ScanReport:
    """Run every detector and produce a fail-closed report."""
    findings: list[ScanFinding] = []

    for rule in SCAN_RULES:
        for match in rule.pattern.finditer(text):
            value = match.group(0)
            if rule.id == "payment-card" and not is_luhn_valid(value):
                continue
            findings.append(
                ScanFinding(
                    rule_id=rule.id,
                    scanner=rule.scanner,
                    label=rule.label,
                    severity=_effective_severity(rule.scanner, rule.severity, kind),
                    excerpt=mask_excerpt(value),
                    index=match.start(),
                    length=len(value),
                )
            )

    findings.extend(_find_high_entropy_spans(text, findings))

    if kind in _JSONL_KINDS:
        findings.extend(_find_invalid_jsonl_lines(text))

    findings.sort(key=lambda finding: finding.index)

    redacted_text, redaction_manifest = apply_redactions(text, findings)

    return ScanReport(
        findings=findings,
        verdict=get_scan_verdict(findings),
        redacted_text=redacted_text,
        redaction_manifest=redaction_manifest,
        scanner_results=_get_scanner_results(findings),
        references=extract_references(text),
    )


def _effective_severity(scanner: ScannerId, severity: ScanSeverity, kind: BundleFileKind | None) -> ScanSeverity:
    if scanner == "malicious" and severity == "reject" and kind in NARRATIVE_KINDS:
        return "review"
    return severity


def apply_redactions(text: str, findings: list[ScanFinding]) -> tuple[str, list[RedactionEntry]]:
    """Replace ``redact`` findings with ``[REDACTED:rule-id]`` placeholders.

    Overlapping spans collapse into the first match (by index, then rule order).
    """
    redactable = sorted(
        (finding for finding in findings if finding.severity == "redact"),
        key=lambda finding: finding.index,
    )

    manifest: dict[str, RedactionEntry] = {}
    pieces: list[str] = []
    cursor = 0

    for finding in redactable:
        if finding.index < cursor:
            continue
        pieces.append(text[cursor : finding.index])
        pieces.append(f"{REDACTION_PREFIX}{finding.rule_id}{REDACTION_SUFFIX}")
        cursor = finding.index + finding.length

        entry = manifest.get(finding.rule_id)
        if entry is None:
            manifest[finding.rule_id] = RedactionEntry(finding.rule_id, finding.label, 1)
        else:
            entry.count += 1

    pieces.append(text[cursor:])
    return "".join(pieces), list(manifest.values())


def shannon_entropy_bits_per_char(token: str) -> float:
    """Shannon entropy in bits per character."""
    if not token:
        return 0.0

    frequencies: dict[str, int] = {}
    for char in token:
        frequencies[char] = frequencies.get(char, 0) + 1

    entropy = 0.0
    length = len(token)
    for count in frequencies.values():
        probability = count / length
        entropy -= probability * math.log2(probability)

    return entropy


def is_luhn_valid(candidate: str) -> bool:
    """Luhn checksum over the digits of a candidate card number."""
    digits = re.sub(r"[^0-9]", "", candidate)
    if len(digits) < 13 or len(digits) > 19:
        return False

    total = 0
    should_double = False
    for char in reversed(digits):
        digit = int(char)
        if should_double:
            digit *= 2
            if digit > 9:
                digit -= 9
        total += digit
        should_double = not should_double

    return total % 10 == 0


def extract_references(text: str) -> ScanReferences:
    """URLs and import targets — reviewer evidence, never a verdict."""
    urls = dict.fromkeys(match.group(0) for match in _URL_REFERENCE_PATTERN.finditer(text))
    dependencies: dict[str, None] = {}
    for match in _PYTHON_IMPORT_PATTERN.finditer(text):
        dependencies[match.group(1)] = None
    for match in _JS_IMPORT_PATTERN.finditer(text):
        dependencies[match.group(1)] = None

    return ScanReferences(urls=list(urls), dependencies=list(dependencies))


def get_scan_verdict(findings: list[ScanFinding]) -> ReviewState:
    """Fail-closed precedence: reject > redact > review."""
    if any(finding.severity == "reject" for finding in findings):
        return "rejected"
    if any(finding.severity == "redact" for finding in findings):
        return "needs_user_redaction"
    return "needs_human_review"


_MODEL_VERDICT_TO_STATE: dict[str, ReviewState] = {
    "reject": "rejected",
    "clear": "needs_human_review",
    "needs_user_redaction": "needs_user_redaction",
    "needs_human_review": "needs_human_review",
}
_VERDICT_STRICTNESS: dict[ReviewState, int] = {
    "rejected": 3,
    "needs_user_redaction": 2,
}


def combine_review_verdicts(deterministic: ReviewState, model_verdict: str) -> ReviewState:
    """Combine tier-1 and model-screening verdicts; the model can only downgrade."""
    model_state = _MODEL_VERDICT_TO_STATE.get(model_verdict, "needs_human_review")
    if _VERDICT_STRICTNESS.get(model_state, 1) > _VERDICT_STRICTNESS.get(deterministic, 1):
        return model_state
    return deterministic


def mask_excerpt(value: str) -> str:
    visible = value[:SCAN_EXCERPT_VISIBLE_CHARS]
    if len(value) <= SCAN_EXCERPT_VISIBLE_CHARS:
        return visible
    return f"{visible}… (+{len(value) - SCAN_EXCERPT_VISIBLE_CHARS} chars)"


def _find_high_entropy_spans(text: str, existing: list[ScanFinding]) -> list[ScanFinding]:
    findings: list[ScanFinding] = []
    for match in _ENTROPY_TOKEN_PATTERN.finditer(text):
        token = match.group(0)
        if len(token) < ENTROPY_MIN_TOKEN_LENGTH:
            continue
        if token.startswith("http://") or token.startswith("https://"):
            continue
        if shannon_entropy_bits_per_char(token) < ENTROPY_THRESHOLD_BITS:
            continue

        start = match.start()
        end = start + len(token)
        overlaps = any(start < finding.index + finding.length and finding.index < end for finding in existing)
        if overlaps:
            continue

        findings.append(
            ScanFinding(
                rule_id=_HIGH_ENTROPY_RULE_ID,
                scanner="encoded",
                label="high-entropy span",
                severity="review",
                excerpt=mask_excerpt(token),
                index=start,
                length=len(token),
            )
        )
    return findings


def _reject_non_finite(_value: str) -> object:
    """parse_constant hook so json.loads rejects bare NaN/Infinity/-Infinity,
    matching JavaScript JSON.parse (which throws on them)."""
    raise ValueError("non-finite JSON constant")


def _find_invalid_jsonl_lines(text: str) -> list[ScanFinding]:
    findings: list[ScanFinding] = []
    offset = 0
    for line in text.split("\n"):
        trimmed = line.strip()
        if trimmed:
            try:
                json.loads(trimmed, parse_constant=_reject_non_finite)
            except ValueError:
                findings.append(
                    ScanFinding(
                        rule_id=_INVALID_JSONL_RULE_ID,
                        scanner="encoded",
                        label="unparseable JSONL line",
                        severity="review",
                        excerpt=mask_excerpt(trimmed),
                        index=offset,
                        length=len(line),
                    )
                )
        offset += len(line) + 1
    return findings


def _get_scanner_results(findings: list[ScanFinding]) -> dict[ScannerId, ScannerResult]:
    flagged = {finding.scanner for finding in findings}
    scanners: tuple[ScannerId, ...] = ("secrets", "pii", "encoded", "malicious")
    return {scanner: ("flagged" if scanner in flagged else "passed") for scanner in scanners}
