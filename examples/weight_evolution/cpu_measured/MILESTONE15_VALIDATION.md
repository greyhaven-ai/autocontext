# Milestone 15 validation

Implemented in the isolated `codex/cpu-measured-outcomes` worktree from merged
M14, `af48af6a1db0bd13277eb5dd65956ab48f2ba930`. GPU execution remains deferred.
The machine-readable results are in [MILESTONE15_RESULTS.json](MILESTONE15_RESULTS.json).

## Implementation

The new failure schema pins the redaction policy, normalization, marker, byte
bounds, and redaction inventory digest. It removes overlapping prompt spans
before clipping and explicitly omits text when a marker or clipping boundary
would reintroduce a redaction term. Rendering failures and invalid encoding are
represented explicitly. Exception cause/context cycles and truncation remain
bounded. Every returned capture passes its replay validator. The checked-in
[golden fixture](../../../autocontext/tests/fixtures/weight_reproduction/cpu_failure_v2.json)
pins the default contract and exact serialized records for marker collisions,
overlaps, Unicode, invalid bytes, cycles and rendering failure.

The measured parent has its own implementation and runtime identity. It reuses
the unchanged fixed CPU worker, checkpoint format, resource limits, native
receipt validator and semantic digest. Its profile pins the weight-evolution
package source inventory, native runtime, interpreter and platform. New files
preserve the M11–M14 implementation digests; no existing execution source was
modified or monkeypatched to claim the old profile.

Process observations preserve bounded stdout/stderr, elapsed intervals, actual
pre-cleanup exit status and nullable sampled RSS with provenance. Cleanup status,
parent signal actions and post-cleanup exit status remain separate. The first
primary error and first cleanup error have independent v2 records; additional
cleanup errors are counted without invented cause links. Linux failed-process
RSS remains unknown when no measurement is available. A parent-issued SIGKILL
does not establish an OOM. Darwin retains the native bounded exit-observation
grace after a monitoring failure. Lost ownership forbids signaling; an exception
before the launch constructor returns a process handle leaves dispatch uncertain.

The acceptance ledger declares exactly two conditions before dispatch: one
native batch and one measured batch on the same four fixed practice requests.
Both run once even if the other condition fails. Claims charge their full
reservation, outcomes precede terminal results, and a retained claim never
dispatches again. A saved outcome can recover interrupted result publication;
missing observations remain uncertain. Reports verify complete or pending
prefixes without writes, inference, retries, refunds or backfill. Returned
outcomes survive a subsequent checkpoint/source drift check.

## Validation

The macOS/ARM64 compatibility run passed 393 tests with nine expected Linux-only
skips. It covered the five new modules, native runner/resource checks and all six
M14 test modules. After the final bounded Darwin grace regression and two
additional ledger tests, the five M15 modules passed **204 tests with three
Linux-only skips**. The unchanged native compatibility modules were not rerun.
These overlapping counts describe separate runs and should not be added.

The final offline Linux/ARM64 run passed **404 tests with one expected Darwin-only
skip**, including all 207 M15 cases. Platform details are recorded in the adjacent
JSON results. The container used the cached pinned Python image, one CPU, a 1 GiB
memory cap, no network, a read-only repository and a separate evidence directory.
Its cases include actual address-space allocation denial, CPU-limit termination,
and descendant cleanup under a Linux subreaper. Test probes are deterministic
fixtures, not a public arbitrary-program execution feature.

Full Python Ruff and mypy checks passed, along with protocol generation,
documentation links and whitespace checks. Tests cover malformed/duplicate JSON,
bounded pipes, nonzero exits, timeouts, monitoring loss, ownership loss, primary
versus cleanup errors, launch uncertainty, phase tampering, profile/checkpoint
drift, corrupted/rehashed journals, pending publication and read-only replay.

The declared macOS and Linux acceptances each passed with identical semantic
receipts from the native and measured profiles. Each charged eight forward calls,
sixteen input tokens and eight output tokens across two batches. They are recorded
separately in the results. Per-host identities differ by design; parity compares the two profiles
within each host. Guarded replay replaces both dispatch functions with failures
and verifies nine unchanged files and identical reports on each host. Linux test
source hashes were unchanged throughout validation. These acceptance
records are distinct from test fixtures and are not a replacement research pilot.

Raw local macOS evidence is retained under `runs/milestone15-validation/` in this
worktree. Linux evidence is retained under
`/private/tmp/autocontext-m15-linux-evidence`; its command and log are beside that
directory. Raw evidence remains outside Git; the checked-in summary retains
contract, source, profile, report, ledger and receipt digests.

## Remaining gaps

- Linux/amd64 CI has not run for M15. Local Linux/ARM64 acceptance does not close
  that architecture-specific gap. M14's Linux/amd64 CI was already green before
  PR #1501 merged.
- The new profile covers the fixed measured batch runner and bounded acceptance
  ledger. A full search/reference campaign under this new measurement profile
  has not been integrated or executed. Existing M11–M14 journals remain on their
  original implementations; no new 72-search pilot was run.
- The original M13 failure's missing message and process measurements cannot be
  recovered retroactively. Neither successful acceptance nor the known fault
  fixtures establish its cause. Missing Linux failure RSS remains null.
- Positive joint-gate control acceptance, scientific transfer/quality claims,
  live proposals, larger models, TypeScript parity and promotion remain deferred.
- Qwen GPU acceptance, full base-versus-patch inference and benchmark reproduction
  remain unexecuted. This milestone performs only tiny fixed-model CPU inference.
