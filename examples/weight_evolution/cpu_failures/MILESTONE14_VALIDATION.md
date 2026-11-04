# Milestone 14 validation

This implementation is isolated in `codex/cpu-failure-evidence`, based on merged
main `14292a45a1ec1fe5910f8513694a1ced7731af5e`. GPU execution remains deferred.

## Contract and implementation

M13's Linux/amd64 CI passed before merge. Its original CPU pilot still has one
unresolved failure in `confirmation-CW`: 71 searches completed, one failed, and
all 24 references remained blocked. The original journal retained only the
exception class. M14 cannot recover its missing message or establish its cause.

M14 adds a separate supervisor contract, host/runtime pin, failure-record
contract, complete search capacity and conditional reference capacity before
dispatch. Native M11–M13 source files and their implementation pins are unchanged.
Task generation, checkpoint materialization, policies, scoring, resource limits,
selected confirmation and the joint C/W/D gate use the existing implementations.

The supervisor captures bounded explicit-cause and implicit-context graphs from
the first live child exception, including cycles, suppressed contexts and
truncation. Records bind the parent contract, native claim/result, operation,
request and checkpoint identities, and declared limits. Raw request prompts are
redacted. The supervisor does not inspect traceback locals or infer a timeout,
OOM, signal or return code from error text. Unavailable return code, signal,
elapsed time, peak RSS and output streams remain null. A terminal operation
binding identifies retained evidence; it does not prove that operation caused
the failure, especially if an exception happened during later publication.

An immutable observation is published before the parent terminal result, which
binds its digest. A previously claimed child is never dispatched again. Resume
can recover a parent result from a completely verified native child, or retain
the failure/uncertainty and full reservation. A lost live observation stays null;
replay never fabricates or backfills it. Native failed prefixes are semantically
verified before later work, including when parent file inventories are rehashed.

Reference plans bind the complete actual supervisor report and ledger, as well
as the native diagnostic contract. The existing pure family-report projection
is reused for scoring and transfer summaries without attributing execution to
an old coordinator. Every source search must complete before plan creation.
Reference observations follow the same publication, replay and no-retry rules.

## Deterministic validation

Fixtures cover wall deadlines, nonzero exits, malformed and oversized output,
stderr, Linux address-space/CPU limits, Darwin monitoring and exit races, cleanup
exceptions, bounded cause graphs, redaction, unknown telemetry and corrupted
bindings. Campaign/reference tests cover interruption before and after
observation/result publication, pre-bootstrap and pending native claims,
completed-child recovery, preserved reservations, source/report tampering and
read-only report purity. These fixed test programs and injected faults are test
fixtures, not arbitrary-program execution features or pilot measurements.

## Declared follow-up pilot

The follow-up uses the same M13 generated tasks, search policies, seeds and
worker limits under a new campaign identifier and supervisor pin. It schedules
12 tasks × two repeats × three policies: 72 searches. Its 24 practice references
are conditional on every source search completing. This is one declared attempt;
no outcome can select a replacement task, seed, limit or additional attempt.

The one macOS/ARM64 follow-up completed all 72 searches and all 24 references.
There were no live failures. All 72 searches were joint-vetoed and audit remained
withheld. Each family had 48 matching calibration checks, for 144 matches, zero
mismatches and zero unavailable checks. These checks compare constructed W
expectations with practice references and selected confirmation; they do not
override the joint gate or establish an adaptive-policy advantage.

Source journals charged 12,096 forward calls, 82,518 input tokens, 360
materializations and 648 proposals within the declared capacity of 14,976
forward calls and 129,288 input tokens. The separate reference phase dispatched
312 batches / 2,496 forward calls and 17,688 input tokens, with no source search
or materialization dispatch. All declared reference capacity was accounted for;
source and reference evidence remain separate.

Task-file bytes were identical to M13's original pilot. The native campaign
contract differed only in `campaign_id`. Execution-blocked resume and reporting
reproduced the retained reports exactly; write guards and file snapshots verified
that reporting and reference execution preserved source evidence. The complete
pilot and replay validation took 413.695 seconds. Raw local evidence is retained
under `runs/milestone14-validation/` and is excluded from Git.

This outcome does not diagnose the earlier failure. Read-only compatibility
replay from the M14 environment matched the retained M11/M12 pilot diagnostic,
M13 fixture diagnostic and M13 failed-pilot reports exactly. All 18,202 retained
files were unchanged, all old implementation pins matched, and execution/write
guards observed zero attempts. M13 still reports 71 completed searches and one
failed search, with its 24 references blocked.

## Regression and Linux checks

All six new M14 modules passed in both full runs: 136 passed / two Linux-only
skips on macOS, and 138 passed on Linux ARM64. Ruff passed for `src`, `tests` and
the example wrappers; mypy passed for 1,022 source files. Markdown link integrity
and whitespace checks passed.

The initial broader macOS run covered 69 modules and 2,333 cases: **2,304 passed,
13 skipped, five failed and 11 setup errors** in 1,150.834 seconds. Its failures
were in older M11/M13 campaign fixtures. One M11 fresh campaign retained an
unexpected failed `confirmation-W` batch; three M13 source searches failed,
which also blocked dependent diagnostic fixtures. Their journals retained only
`CpuRunnerError`. All 1,091 source, test and configuration file hashes were
unchanged.

The initial offline Linux run covered all 27 CPU modules: **834 passed, one
failed and one Darwin-only skip** in 1,406.33 seconds. The failure was the older
M13 test of failed-search denominators, where three extra searches failed in
addition to the intentionally injected failure. All 154 source, test, lock,
bundle and wrapper hashes were unchanged. The container used cached dependencies
and a pinned image, no network, a read-only repository mount, one CPU, a 1 GiB
memory limit and a 1,800-second pytest watchdog. It ran CPython 3.11.16 on Linux
ARM64; this does not substitute for Linux/amd64 CI.

The full suites overlapped on a heavily loaded host. Several unexpected native
claim-to-result timestamp intervals were near or above the unchanged five-second
worker deadline. These are filesystem timestamps, not measured worker telemetry,
so they suggest contention without establishing a cause. The original failing
journals and initial test receipts are retained. Separate serial rechecks of only
the failing test modules use fresh fixtures, unchanged source/limits and bounded
test-only exception logging. They do not retry the declared pilot or alter these
initial results. The macOS recheck passed all 42 tests across the two failing
modules in 202.831 seconds, with zero live boundary exception observations and
all 1,091 hashes matching the initial run. The Linux recheck passed all 18 control
tests in 147.92 seconds. Its observer forwarded 448 native batch calls and
captured zero failures, dropped records or observer errors; all source hashes
matched the initial run. Passing a recheck does not establish the cause of an
earlier failure or turn the initial full run green.

Pinned identities, capacities, family summaries, compatibility snapshots and
test results are recorded in [MILESTONE14_RESULTS.json](MILESTONE14_RESULTS.json).

## Remaining scope

- The original M13 failure remains unexplained; future observations cannot
  retroactively supply evidence that was never recorded.
- M14 still needs Linux/amd64 GitHub CI after publication. Earlier M13 CI does
  not validate new supervisor code. Local Linux acceptance uses ARM64.
- Exception text is an observation, not independently established root-cause
  attribution. Historical class-only records and interruptions can remain
  uninformative. Failure telemetry is not substituted for unknown budget usage.
- Prompt redaction is validated for the pinned generated tasks, whose prompts
  contain multiple `token0`–`token7` tokens. The lower-level capture helper is not
  ready for arbitrary prompts: a short prompt such as `a` or `prompt` can occur
  in its redaction marker and cause replay rejection. Supporting those inputs
  requires a safe fallback and regression cases under a new implementation pin.
- The native worker runs fixed trusted code. macOS uses sampled RSS with possible
  overshoot; Linux applies address-space/CPU limits and parent wall deadlines.
  Local hashes are not externally signed evidence or protection against a
  noncooperating writer who can replace all bound records.
- These constructed CPU controls do not establish language-model generalization,
  adaptive-policy superiority or natural-task benchmark gains. GPU/Qwen
  acceptance and the published base-versus-patch reproduction remain unexecuted.
  Larger models, live proposals, arbitrary programs, TypeScript runtime parity
  and promotion remain deferred. No GPU or paid remote compute was used.
