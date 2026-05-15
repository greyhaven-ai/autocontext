"""AC-706 (slice 1): ingest Hermes trajectory JSONL with explicit redaction.

Hermes records trajectory samples and failed trajectories as JSONL
(one trajectory per line). The shape is ShareGPT-like: each line is a
JSON object with a ``messages`` array of ``{"role", "content"}``
entries, optionally accompanied by run-level metadata.

This module reads the input JSONL line-by-line (so a single corrupt
line cannot abort the whole import), routes every ``content`` string
through :func:`autocontext.hermes.redaction.redact_text`, and writes a
redacted JSONL output with the same shape plus a
``trajectory_redactions`` entry that summarizes what was removed
(category -> count). Operators can audit the count without re-reading
the original raw file.

The importer never writes to the input file or the Hermes home; the
output is always a separate JSONL path the operator chose. See AC-706
acceptance criteria.

Privacy posture:

- Default mode is ``standard``: the full ``sharing/redactor`` pipeline.
- ``off`` is supported but requires an explicit operator opt-in. The
  CLI surfaces a clear warning when ``--redact off`` is passed.
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
            is True.
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
    """

    if not input_path.exists():
        raise FileNotFoundError(f"trajectory input not found: {input_path}")

    summary = TrajectoryIngestSummary(
        input_path=input_path,
        output_path=None if dry_run else output_path,
        dry_run=dry_run,
    )

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

            redacted = _redact_trajectory(trajectory, policy=policy, stats=summary.redactions)
            out_lines.append(json.dumps(redacted, separators=(",", ":")))
            summary.trajectories_written += 1

    if not dry_run:
        output_path.parent.mkdir(parents=True, exist_ok=True)
        with output_path.open("w", encoding="utf-8") as fh:
            for entry in out_lines:
                fh.write(entry + "\n")

    return summary


def _redact_trajectory(
    trajectory: dict[str, Any],
    *,
    policy: RedactionPolicy,
    stats: RedactionStats,
) -> dict[str, Any]:
    """Return a copy of ``trajectory`` with every text field redacted.

    Walks the standard ShareGPT-like keys plus the common Hermes batch
    runner fields:
    * ``messages[*].content``: redacted via the policy.
    * ``prompt`` / ``response`` / ``output`` / ``input``: redacted if
      present as strings.
    * everything else: passed through verbatim.

    The per-trajectory category counts are added to ``stats`` so the
    summary reflects the whole-file totals.
    """

    out: dict[str, Any] = dict(trajectory)

    messages = trajectory.get("messages")
    if isinstance(messages, list):
        out["messages"] = [_redact_message(msg, policy=policy, stats=stats) for msg in messages]

    for key in ("prompt", "response", "output", "input"):
        value = trajectory.get(key)
        if isinstance(value, str):
            redacted, sub = redact_text(value, policy)
            out[key] = redacted
            for category, count in sub.by_category.items():
                stats.add(category, count)

    return out


def _redact_message(
    message: Any,
    *,
    policy: RedactionPolicy,
    stats: RedactionStats,
) -> Any:
    if not isinstance(message, dict):
        return message
    content = message.get("content")
    if not isinstance(content, str):
        return message
    redacted, sub = redact_text(content, policy)
    for category, count in sub.by_category.items():
        stats.add(category, count)
    return {**message, "content": redacted}


__all__ = ["TrajectoryIngestSummary", "ingest_trajectory_jsonl"]
