"""AC-706 (slice 1): ingest Hermes trajectory JSONL with explicit redaction.

Hermes records trajectory samples and failed trajectories as JSONL
(one trajectory per line). The shape is ShareGPT-like: each line is a
JSON object with a ``messages`` array of ``{"role", "content"}``
entries, optionally accompanied by run-level metadata.

This module reads the input JSONL line-by-line (so a single corrupt
line cannot abort the whole import), routes every string content
through :func:`autocontext.hermes.redaction.redact_text`, and writes a
redacted JSONL output with the same shape plus a
``trajectory_redactions`` entry on every row that summarizes what was
removed (category -> count). Operators can audit the count without
re-reading the original raw file.

The importer never writes to the input file or the Hermes home; the
output is always a separate JSONL path the operator chose. Passing
the same path for ``--input`` and ``--output`` (or two paths that
resolve to the same file) is rejected at the boundary. See AC-706
acceptance criteria.

Content shapes the redactor walks:

* ``messages[*].content`` as a string: redacted directly.
* ``messages[*].content`` as a list of content blocks
  (OpenAI/Anthropic-style ``[{"type": "text", "text": "..."}]``):
  every string leaf in the block is redacted, so secrets cannot hide
  inside ``text`` / ``input`` / ``output`` fields of structured
  blocks.
* ``prompt`` / ``response`` / ``output`` / ``input`` top-level
  strings: redacted in place.
* everything else: passed through verbatim.

Privacy posture:

- Default mode is ``standard``: the full ``sharing/redactor`` pipeline.
- ``off`` is supported but requires an explicit operator opt-in. The
  ingester records a policy warning in ``summary.warnings`` whenever
  ``off`` is used so JSON callers and audit logs see the opt-in
  marker as well as the CLI's human-mode warning.
- ``--dry-run`` returns the counts and redaction stats without writing
  the output file. AC-706 calls for this so operators can review the
  blast radius before committing content to disk.
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from autocontext.hermes.redaction import RedactionPolicy, RedactionStats, redact_text

# Marker the CLI surfaces and JSON callers can match on so automation
# knows raw content was written without parsing free-form warning text.
RAW_CONTENT_WARNING = "policy=off: raw content written; AC-706 requires explicit operator opt-in"


@dataclass(slots=True)
class TrajectoryIngestSummary:
    """What happened during a single trajectory ingest call."""

    input_path: Path
    output_path: Path | None
    lines_read: int = 0
    trajectories_written: int = 0
    skipped: int = 0
    warnings: list[str] = field(default_factory=list)
    redactions: RedactionStats = field(default_factory=RedactionStats)
    dry_run: bool = False

    def to_dict(self) -> dict[str, Any]:
        return {
            "input_path": str(self.input_path),
            "output_path": str(self.output_path) if self.output_path is not None else None,
            "lines_read": self.lines_read,
            "trajectories_written": self.trajectories_written,
            "skipped": self.skipped,
            "warnings": list(self.warnings),
            "redactions": self.redactions.to_dict(),
            "dry_run": self.dry_run,
        }


def ingest_trajectory_jsonl(
    *,
    input_path: Path,
    output_path: Path,
    policy: RedactionPolicy,
    limit: int | None = None,
    dry_run: bool = False,
) -> TrajectoryIngestSummary:
    """Read ShareGPT-like trajectory JSONL and write a redacted copy.

    Args:
        input_path: source JSONL (``trajectory_samples.jsonl``,
            ``failed_trajectories.jsonl``, or a batch runner export).
        output_path: where to write the redacted JSONL. The parent
            directory is created if missing. Ignored when ``dry_run``
            is True. Must not resolve to the same file as
            ``input_path`` (the importer never mutates Hermes
            artifacts).
        policy: redaction policy (mode + optional user patterns).
        limit: cap on the number of trajectories written. Useful for
            sampling before a full import.
        dry_run: when True, count and redact but do not write the
            output file. The summary's ``redactions`` field still
            reflects what would have been removed.

    Returns:
        :class:`TrajectoryIngestSummary` with counts, warnings, and
        per-category redaction stats.

    Raises:
        FileNotFoundError: if ``input_path`` does not exist.
        ValueError: if ``output_path`` resolves to the same file as
            ``input_path`` (would overwrite the source, violating
            AC-706's "input file never modified" invariant).
    """

    if not input_path.exists():
        raise FileNotFoundError(f"trajectory input not found: {input_path}")

    if not dry_run and _same_file(input_path, output_path):
        raise ValueError(
            f"output {output_path!s} resolves to the same file as input {input_path!s}; "
            "refusing to overwrite the source trajectory (AC-706 invariant)"
        )

    summary = TrajectoryIngestSummary(
        input_path=input_path,
        output_path=None if dry_run else output_path,
        dry_run=dry_run,
    )
    if policy.mode == "off":
        # JSON callers cannot see the CLI's human-mode warning; record
        # the opt-in marker in the summary so automation can match on
        # it (PR review P3).
        summary.warnings.append(RAW_CONTENT_WARNING)

    out_lines: list[str] = []
    with input_path.open("r", encoding="utf-8") as fh:
        for raw_line in fh:
            line = raw_line.rstrip("\n")
            if not line.strip():
                continue
            summary.lines_read += 1
            if limit is not None and summary.trajectories_written >= limit:
                continue
            try:
                trajectory = json.loads(line)
            except json.JSONDecodeError as err:
                summary.skipped += 1
                summary.warnings.append(f"line {summary.lines_read}: malformed JSON ({err.msg})")
                continue
            if not isinstance(trajectory, dict):
                summary.skipped += 1
                summary.warnings.append(f"line {summary.lines_read}: trajectory must be a JSON object")
                continue

            redacted, per_row_stats = _redact_trajectory(trajectory, policy=policy)
            # Per-row audit trail (PR review P2): each output row carries
            # its own redaction count breakdown so downstream consumers
            # can match a row to what was removed from it without the
            # CLI summary.
            redacted["trajectory_redactions"] = per_row_stats.to_dict()
            for category, count in per_row_stats.by_category.items():
                summary.redactions.add(category, count)
            out_lines.append(json.dumps(redacted, separators=(",", ":")))
            summary.trajectories_written += 1

    if not dry_run:
        output_path.parent.mkdir(parents=True, exist_ok=True)
        with output_path.open("w", encoding="utf-8") as fh:
            for entry in out_lines:
                fh.write(entry + "\n")

    return summary


def _same_file(a: Path, b: Path) -> bool:
    """Return True when ``a`` and ``b`` point at the same file.

    Uses :py:meth:`Path.samefile` when both exist (handles symlinks and
    hardlinks). Falls back to resolved-path equality when ``b`` does
    not exist yet, which catches the common "operator typed the same
    path twice" case before any read or write.
    """
    if a.exists() and b.exists():
        try:
            return a.samefile(b)
        except OSError:
            return False
    return a.resolve() == b.resolve()


def _redact_trajectory(
    trajectory: dict[str, Any],
    *,
    policy: RedactionPolicy,
) -> tuple[dict[str, Any], RedactionStats]:
    """Return ``(redacted_trajectory, per_row_stats)``.

    Walks the standard ShareGPT-like keys plus the common Hermes batch
    runner fields:

    * ``messages[*].content``: string content is redacted via the
      policy; structured content blocks
      (``[{"type": "text", "text": "..."}]``) have every string leaf
      redacted recursively, so secrets inside ``text`` / ``input``
      fields of OpenAI/Anthropic-style blocks cannot pass through
      unredacted (PR review P2).
    * ``prompt`` / ``response`` / ``output`` / ``input``: redacted if
      present as strings.
    * everything else: passed through verbatim.
    """

    out: dict[str, Any] = dict(trajectory)
    stats = RedactionStats()

    messages = trajectory.get("messages")
    if isinstance(messages, list):
        out["messages"] = [_redact_message(msg, policy=policy, stats=stats) for msg in messages]

    for key in ("prompt", "response", "output", "input"):
        value = trajectory.get(key)
        if isinstance(value, str):
            redacted, sub = redact_text(value, policy)
            out[key] = redacted
            _accumulate(stats, sub)

    return out, stats


def _redact_message(
    message: Any,
    *,
    policy: RedactionPolicy,
    stats: RedactionStats,
) -> Any:
    if not isinstance(message, dict):
        return message
    content = message.get("content")
    if content is None:
        return message
    new_content = _redact_value(content, policy=policy, stats=stats)
    return {**message, "content": new_content}


def _redact_value(value: Any, *, policy: RedactionPolicy, stats: RedactionStats) -> Any:
    """Recursively redact string leaves while preserving structure.

    Used for both message content (which may be a string or a list of
    structured content blocks) and for nested fields inside those
    blocks. Non-string values are returned unchanged; lists and dicts
    are walked.
    """
    if isinstance(value, str):
        redacted, sub = redact_text(value, policy)
        _accumulate(stats, sub)
        return redacted
    if isinstance(value, list):
        return [_redact_value(item, policy=policy, stats=stats) for item in value]
    if isinstance(value, dict):
        return {k: _redact_value(v, policy=policy, stats=stats) for k, v in value.items()}
    return value


def _accumulate(target: RedactionStats, source: RedactionStats) -> None:
    for category, count in source.by_category.items():
        target.add(category, count)


__all__ = ["RAW_CONTENT_WARNING", "TrajectoryIngestSummary", "ingest_trajectory_jsonl"]
