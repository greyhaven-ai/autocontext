# Changelog

All notable changes to this project will be documented in this file.

## [Unreleased]

### Added

- AC-749 (slice): `autoctx analytics render-timeline --trace-id <id> [--output path.html]` renders an existing persisted `RunTrace` as an interactive HTML timeline. On-demand counterpart to the run-end-time renderer that already lives in `loop/trace_artifacts.persist_run_inspection`; reuses the same `timeline_inspection_view` extractor + `render_timeline_inspection_html` view. The rendered HTML now also surfaces a "Generations" section with per-generation failure/recovery counts (data attributes `data-generation-index`, `data-generation-failure-count`, `data-generation-recovery-count` for client-side hooks). The view layer exposes the same `inspect_generation` data the JSON payload already carries -- no new analytics model.
- Harness proposal decisions now require explicit evidence references before heldout/fresh validation can accept or reject a proposal. Missing `--evidence-ref` keeps the durable decision `inconclusive`, and corrupted accepted/rejected proposal JSON with empty `evidenceRefs`, dev-only evidence, or missing baseline evidence is rejected by schema validation.
- AC-727 (slice): `autoctx improve --checkpoint-cmd` runs a user-supplied command after each round to preserve partial progress (e.g. `git -C /repo commit -am 'round checkpoint'` or `cp {file} /tmp/round.lean`). Same `{file}` placeholder semantics as `--verify-cmd`, plus `--checkpoint-suffix` and `--checkpoint-timeout` companions. Unlike the verifier, a checkpoint command's non-zero exit is logged but does NOT veto the round; it surfaces as a new `checkpoint_done(round=N, checkpoint_ok=..., checkpoint_exit_code=...)` event in the `--ndjson` stream. Lets long-running improve loops salvage near-miss artifacts before later rounds overshoot or time out.
- AC-723: the TypeScript CLI now exposes `autoctx agent run <agent>` and `autoctx agent dev` for experimental `.autoctx/agents` handlers. The one-shot runner accepts `--id`, JSON `--payload`, explicit `--env` files with shell env precedence, provider/model overrides for runtime-backed handlers, and `--json` output; the dev server exposes `GET /manifest` and `POST /agents/<name>/invoke`.
- Context-selection analytics reports now include actionable diagnostics for duplicate selected content, low useful-artifact recall, and selected-token bloat.
- Python analytics now includes `autoctx analytics context-selection --run-id <run-id> [--json]` to summarize persisted context-selection artifacts by selected tokens, selection rate, duplicate-content rate, useful-artifact recall, and freshness.
- AC-757: TypeScript control-plane EvalRuns now support `verified` and `experimental` tracks. `autoctx eval attach` accepts `--track verified|experimental`, `eval list --output json` reports the effective track, and promotion decisions reject explicitly experimental EvalRuns as non-promotion evidence.
- AC-758: Candidate artifacts now record deterministic strategy identity metadata: a canonical strategy fingerprint, component fingerprints, parent strategy lineage, and exact/near duplicate assessment. `autoctx candidate register/show` include the metadata, and `candidate list` surfaces the strategy fingerprint and duplicate kind.
- AC-759: Candidate artifacts now quarantine repeated invalid strategies by fingerprint. Re-registering an exact or near duplicate of a disabled/quarantined strategy records `strategyQuarantine`, `candidate list` surfaces `quarantineReason`, promotion decisions reject quarantined strategies, and operational memory skips findings tied to quarantined strategy fingerprints.
- AC-760: EvalRuns can now carry opt-in ablation verification evidence for accepted strategy and harness changes. `autoctx eval attach` accepts `--ablation-verification ./ablation.json`, `promotion decide --require-ablation` records an `ablationVerification` assessment, and `--ablation-targets strategy,harness` narrows the required target coverage.
- AC-680: TypeScript control-plane harness/context changes now have a durable `HarnessChangeProposal` workflow. `autoctx harness proposal create/list/show/decide` records finding lineage, proposed patches, expected impact, rollback criteria, and an evidence-gated decision that accepts only heldout/fresh validation against matching-suite baseline evidence.
- Strategy duplicate and quarantine checks now span all environments for the same scenario/actuator and use `payloadHash` as an exact-match fallback for legacy artifacts without `strategyIdentity`.
- AC-752: `autoctx improve --ndjson` streams per-round events as newline-delimited JSON to stdout for visibility into long-running loops. Event kinds: `round_start`, `judge_done`, `verifier_done` (only when `--verify-cmd` is set), `round_summary`, and a final summary line. Under `--ndjson` the Rich human-readable summary is suppressed so stdout is pure JSON. `--json` and `--ndjson` are mutually exclusive output modes and are rejected up front when both are passed.
- AC-753: the ndjson stream now also emits a `revision_done(round=N, output=<content>)` event right after `round_start` for every round, carrying the exact output the loop is about to evaluate. For round 1 the payload is the seed; for round N>1 it is the result of `task.revise_output()` from round N-1. Lets consumers salvage near-miss verifier-vetoed rounds. Pass `--no-ndjson-include-output` (default `--ndjson-include-output`) to suppress these events when the bulk output is unwanted; that flag drops the `revision_done` event entirely and never writes the output payload anywhere on stdout.
- AC-751: `autoctx improve --claude-max-total-seconds FLOAT` exposes `settings.claude_max_total_seconds` (the wall-clock ceiling on total claude-cli runtime in a single run; env: `AUTOCONTEXT_CLAUDE_MAX_TOTAL_SECONDS`). Only applied when the effectively-resolved judge provider is claude-cli; `judge_provider='auto'` paths that inherit `agent_provider='claude-cli'` are honored. `--timeout` help on `improve` now explicitly names the per-provider setting it writes (`claude_timeout`/`codex_timeout`/`pi_timeout`).
- Python and TypeScript now expose `autoctx worker` to run the existing task queue `TaskRunner` as a daemon or one-shot batch worker, with persistent-host deployment docs for `serve + worker`.
- Added narrow Python/TypeScript task queue store contracts so future hosted storage adapters can provide Postgres-backed claim/complete/fail/enqueue semantics without changing `TaskRunner`.
- Gondolin is documented as a reserved optional microVM sandbox backend, fails closed until a real adapter is configured, and now has public request/policy/backend contracts for out-of-tree adapters.
- TypeScript `autoctx runtime-sessions` now lists, shows, and renders operator-facing timelines for persisted runtime-session event logs from CLI-backed provider runs, including `show --run-id <run-id>` and `timeline --run-id <run-id>` for run-scoped logs; `status`, `show`, and `watch --json` surface a `runtime_session` summary when one exists, MCP exposes the same read surface via `list_runtime_sessions`, `get_runtime_session`, and `get_runtime_session_timeline`, cockpit HTTP clients can read logs and timelines from `/api/cockpit/runtime-sessions`, `/api/cockpit/runtime-sessions/:session_id/timeline`, `/api/cockpit/runs/:run_id/runtime-session`, and `/api/cockpit/runs/:run_id/runtime-session/timeline`, cockpit run list/status/resume payloads include `runtime_session` plus `runtime_session_url` for discovery, the interactive TUI exposes `/timeline <run-id>` for the same grouped view and summarizes live runtime-session activity as it arrives with persisted `/activity` filters, quiet/normal/verbose detail controls, `/activity reset`, read-only bare `/activity` and `/activity status`, and startup readback of loaded activity settings, and `/ws/events` streams live `runtime_session_event` envelopes as runtime-session events are appended.
- Python now has parity readers for runtime-session event logs: a TypeScript-compatible event/store/read-model/timeline layer, cockpit endpoints for listing logs and resolving run-scoped timelines, run list/status/resume discovery fields, and MCP tools `autocontext_list_runtime_sessions`, `autocontext_get_runtime_session`, and `autocontext_get_runtime_session_timeline` with unprefixed aliases.
- Python runtime-backed run and solve role calls now automatically append provider prompts and responses to the run-scoped runtime-session log, preserving runtime failure semantics while making the new Python readers useful without manual recorder wiring.
- Python now exposes a core `RuntimeWorkspaceEnv` contract with local filesystem and in-memory adapters, virtual path resolution, scoped command grants, and explicit cleanup semantics to match the TypeScript runtime workspace boundary.
- TypeScript runtime workspace command grants now expose structured start/end/error observability events, a no-shell local process wrapper with explicit env inheritance, redacted/truncated command output previews, child-task inheritance policy, and scoped command/tool grant types for runtime-session calls without serializing trusted env values into prompts or session logs.
- The canonical concept model now documents durable runtime-session event storage as an `Artifact` model for provider turns, shell/tool activity, child-task lineage, compaction summaries, replay, and the boundary with `RunTrace`/production traces.
- Python and TypeScript runtime-session logs now record semantic compaction ledger writes as `COMPACTION` events with entry ids, component names, ledger paths, and generation metadata for replay timelines; TypeScript records the hook-finalized ledger entries and paths after artifact write hooks run.
- Python and TypeScript now expose explicit runtime-session-to-`RunTrace` adapters for analytics reuse, mapping child-task lineage, command/tool status, and compaction artifact references without copying raw prompts, model responses, stdout/stderr, or arbitrary runtime metadata.

### Fixed

- AC-761 / AC-735: claude-cli subprocesses are now hard-killed at their process group on timeout AND on any other abnormal exit (`KeyboardInterrupt`, `SystemExit`, ...). The previous code path used `subprocess.run(..., timeout=...)`, which only `proc.kill()`s the immediate child; claude-cli helper processes that inherit pipe fds kept the post-kill `communicate()` drain open, so a `--timeout 1200` invocation observed at 2h24m alive (AC-761) and `AUTOCONTEXT_CLAUDE_MAX_TOTAL_SECONDS=28800` runs observed at 8h45m (AC-735). The runtime now spawns claude in its own session (`start_new_session=True`) and `os.killpg(pgid, SIGKILL)`s the whole group, with a bounded 5s grace on the post-kill drain. Because `start_new_session=True` also detaches the child from the terminal's signal-delivery group, Ctrl-C / SIGINT no longer reaches the claude process group automatically; the helper's `except BaseException` branch (PR #940 review) ensures interrupted runs still clean up the detached children before re-raising. Wall-clock returns within `claude_timeout + 5s` even when grandchildren hold pipes open. POSIX only; Windows uses `proc.kill()` fallback.
- AC-756: `ImprovementResult.met_threshold` now consistently mirrors the same predicate used by the early-return paths -- the best round both cleared `quality_threshold` and satisfied `dimension_threshold` if one was configured. Previously the fallthrough exit (plateau-stall, unchanged-output, max-rounds, consecutive-failures) hard-coded `met_threshold=False`, so a run that produced above-threshold output via, e.g., a plateau-stall path was flagged as "didn't meet threshold" and could be discarded by automation. The fix tracks `best_dims_ok` alongside `best_score` so the per-dimension gate is honored at fallthrough exits too.
- AC-754: `ImprovementLoop` now peels off an outer markdown code fence (e.g. ` ```lean ... ``` `) when cleaning agent output, so verifiers that compile the output directly (`lake env lean`, `mypy`, `cargo check`, ...) no longer reject otherwise-valid content on the literal fence lines. Applied to both the seed (round 1's input) and the result of every `task.revise_output()` call. The strip is conservative: only the outer wrapper is removed, inner nested fences and unbalanced fences are preserved.
- AC-750: `ImprovementLoop` no longer fires a misleading `max_score_delta` warning when the previous round was zeroed by the external `--verify-cmd` verifier. The loop now tracks `last_unvetoed_score` separately from `prev_valid_score`; the delta check compares against the last legitimate judge score, while plateau detection still treats consecutive verifier vetoes as a stall.
- Runtime-session event stores now preserve existing events when saving stale or partial logs, and the TypeScript timeline pairs repeated child-task completions by child session id before falling back to task aliases.
- Worker commands now clamp concurrency to one for stateful persistent runtimes, and Python runtime-bridge providers close underlying runtimes on shutdown.
- TypeScript task runners now await queue-store methods so hosted Postgres adapters can implement the queue contract asynchronously.
- AC-733..AC-738 batch from the putnam_2013_a5 stress test: `improve` now exposes `--verify-cmd`/`--verify-suffix`/`--verify-timeout` for compile/test gates that can force score=0 and feed stderr back into revision; `solve` accepts `--task-prompt` to bypass the LLM scenario designer (which truncated long Lean/Putnam-style prompts), `--task-file` for file-backed descriptions, `--generations` as an alias for `--gens`, and `-d` short form for `--description`; `--family` typos surface a `did_you_mean` suggestion via the new `FamilyName` value object instead of silently falling through; `AUTOCONTEXT_CLAUDE_TOOLS=""` now renders as a single `--tools=` argv token rather than a stray double-space; and `AUTOCONTEXT_CLAUDE_MAX_TOTAL_SECONDS` (default `0`/off) attaches a `RuntimeBudget` to every settings-driven `ClaudeCLIRuntime` (default agent provider, per-role overrides, and the judge/provider registry path), with retry backoff sleeps bounded by both the per-invocation cap and the attached budget.

### Changed

- Default of `AUTOCONTEXT_CLAUDE_MAX_TOTAL_SECONDS` is now `0` (disabled, opt-in). Set explicitly when you want a wall-clock cap on total Claude CLI runtime; the per-invocation retry cap inside `ClaudeCLIConfig` keeps its 25-minute default for in-process retry sequences.

## [0.5.0] - 2026-05-01

### Added

- Python and TypeScript `autoctx solve` now accept the plain-language goal as a positional argument while keeping `--description` as a named option.
- Python and TypeScript `solve`/`run` commands now accept `--iterations` as the plain-language alias for `--gens`.
- Python and TypeScript `autoctx run <scenario>` now accept a positional scenario while keeping `--scenario` for scripts.
- Python and TypeScript `autoctx export <run-id>` now export knowledge from a specific run while keeping scenario-level export support.
- TypeScript CLI/TUI help now uses the same plain-language run vocabulary, including `status <run-id>`, `show <run-id> --best`, and `watch <run-id>`.
- Python `autoctx hermes inspect` now reads Hermes v0.12 skill usage telemetry and Curator reports without mutating `~/.hermes`, and `autoctx hermes export-skill` emits a first-class Hermes `autocontext` skill that teaches CLI-first workflows with MCP as optional.

### Fixed

- Python installed `autoctx` no longer crashes on no-args startup when packaged banner assets are missing.

### Changed

- Python `autocontext` and TypeScript `autoctx` package metadata are bumped to `0.5.0`.
- Pi `pi-autocontext` package metadata is bumped to `0.2.4`, and its `autoctx` dependency range accepts both the current `0.4.9` package and the upcoming `0.5.0` npm line.

## [0.4.9] - 2026-04-30

### Fixed

- TypeScript `simulate` now uses the schema-evolution scenario designer for schema-evolution prompts and rejects zero-mutation generated specs before persistence (AC-694).
- Python Pi/Pi-RPC budget errors now report the effective bounded role timeout instead of the original unbounded Pi timeout (AC-695).
- RLM sessions can soft-finalize from explicit final-answer tags, cautious natural-language closure cues, and repeated silent no-progress turns, while preserving real inspection progress (AC-696).
- Rubric drift monitoring now flags within-generation mean-versus-best compression and catches slower dimension decline patterns (AC-686).

### Changed

- Python `autocontext` and TypeScript `autoctx` package metadata are bumped to `0.4.9`.
- Pi `pi-autocontext` package metadata is bumped to `0.2.3` while intentionally keeping its `autoctx` dependency one package behind at `^0.4.8`.

## [0.4.8] - 2026-04-30

### Fixed

- TypeScript generated `schema_evolution` scenarios no longer score empty mutation plans as perfect, and generated actions now record mutation lineage before schema-coverage scoring (AC-666).
- Python Claude CLI runtime calls now use bounded timeout retries with exponential backoff, total wall-clock caps, retry metadata, and warning/error logs for long-running live-agent calls (AC-684).
- Python solve now enforces generation budgets across Pi/Pi-RPC role calls, including per-role overrides, and closes one-shot budgeted persistent Pi RPC clients after use (AC-691).
- TypeScript schema-evolution creation now recovers from Pi-style invalid JSON responses with markdown fences, prose wrappers, comments, trailing commas, and camelCase fields (AC-692).
- Python solve JSON/status output now includes resolved scenario-family metadata for stress harnesses and user workflows (AC-693).
- Iterative investigation no longer requires resolving the architect runtime before the first analyst step.
- Task-like solve lifecycle hooks now report persisted generation counts separately from improvement rounds.

### Changed

- Python `autocontext` and TypeScript `autoctx` package metadata are bumped to `0.4.8`.
- Pi `pi-autocontext` package metadata is bumped to `0.2.2` while intentionally keeping its `autoctx` dependency one package behind at `^0.4.7`.

## [0.4.7] - 2026-04-29

### Added

- Python `autoctx export` now accepts `--format pi-package` to write a Pi-local package directory with `package.json`, `SKILL.md`, prompt markdown, and the original autocontext strategy payload.
- Python and TypeScript autocontext now expose Pi-shaped extension hook buses via `AUTOCONTEXT_EXTENSIONS`, covering run/generation lifecycle, context transforms, semantic compaction, provider requests/responses, judge calls, and artifact writes.
- Pi `pi-autocontext` now exposes `autocontext_runtime_snapshot` for run artifacts, package provenance, session branch lineage, and recent event-stream context.
- TypeScript Pi RPC now supports an opt-in persistent runtime via `AUTOCONTEXT_PI_RPC_PERSISTENT=true`, reusing one `pi --mode rpc` subprocess for prompt and live-control calls.
- TypeScript CLI now exposes `autoctx solve` as a DB-backed solve-on-demand entrypoint with `--description`, `--gens`, `--timeout`, and `--json` support (AC-619).
- TypeScript solve now preserves Python-shaped controls for structured family overrides, per-generation runtime-budget enforcement, output file writing, and classifier fallback status metadata (AC-620).

### Fixed

- TypeScript capabilities now report the provider factory support surface and no longer mark the visible `train` command as Python-only (AC-626).
- TypeScript `run` now supports saved custom `agent_task` scenarios through the agent-task improvement runner instead of rejecting scenarios already discoverable in the control plane (AC-625).

### Changed

- Restructured the top-level `README.md`: leads with the Pi runtime quick start, adds an MCP-driven natural-language entry path ("Or Just Talk To Your Agent"), shows a structured artifact tree with concrete `playbook.md` and `trace.jsonl` excerpts, surfaces production-trace capture as its own section, merges the surfaces table with command examples, and adds a short FAQ. Removes redundant "How People Use It" / "Choose An Entry Point" / "Repository Layout" sections (the last is already covered in `AGENTS.md`).
- Bumped subpackage README references from `0.4.4` to `0.4.7` (`autocontext/README.md`, `ts/README.md`) to track the next release line.
- Python `autocontext`, TypeScript `autoctx`, and Pi `pi-autocontext` package metadata are bumped for the release.

## [0.4.6] - 2026-04-23

### Added

- **Browser integration surface** (AC-598–603): Chrome CDP backend for Python (`autocontext.integrations.browser`) and TypeScript (`autoctx/integrations/browser`), wired into investigations and the task queue. Includes a browser exploration contract, cross-runtime validation fixtures, parity enforcement, and selector generation for CDP element refs.
- **A2-III Anthropic integration**: `instrument_client` / `InstrumentedAsyncAnthropic` (Python) and `instrumentClient` (TypeScript) intercept Anthropic SDK calls and route production traces through the autocontext pipeline, with `AnthropicStreamProxy`/`AnthropicStreamProxyAsync` for streaming and `AnthropicTaxonomyMapper` for outcome classification. Available at `autocontext.integrations.anthropic` and `autoctx/integrations/anthropic`. Includes cross-runtime parity (9 fixtures + 50-run property tests), anthropic-python/ts detector plugins, bundle-size enforcement, and zero-telemetry guarantee.
- **Production traces `build-dataset` filters** (AC-606): `--provider`, `--app`, `--env`, and `--outcome` filters on the `build-dataset` CLI and MCP tool, plus an E2E integration test covering OpenAI + Anthropic traces through ingest→build-dataset.
- Hierarchical investigation evidence with evidence cards cache and artifact drill-down hardening.
- Tail context preservation in secondary prompt reducer surfaces.
- Solve runtime floor raised for generated scenarios.

### Fixed

- Provider proxy runtime plumbing centralized into a shared `_shared/proxy-runtime` module so Anthropic and OpenAI integration proxies share consistent lifecycle and error handling (AC-611).
- TypeScript scenario family designers now share response parsing across agent-task, artifact-editing, and tool-fragility families so generated specs preserve family-specific semantics (AC-612).
- Install salt identity invariant preserved across process restarts (AC-609).
- Cross-runtime migration ledger reconciliation so Python and TypeScript DBs stay aligned after schema divergence (AC-608).
- CLI dispatch moved into a command registry so mission routes resolve correctly (AC-610).
- Babel reverse solve designer retries restored and scenario creation stabilized (AC-607).

### Changed

- Python and TypeScript package metadata are bumped to `0.4.6`.

## [0.4.5] - 2026-04-21

### Fixed

- `quality_threshold` auto-heal no longer silently drops below the configured floor during multi-round improvement loops (AC-585).
- Judge-provider inheritance now propagates correctly to nested evaluation calls so role-routing overrides are honored end-to-end (AC-586).
- Claude CLI timeout default bumped from 300 to 600 seconds, reducing spurious failures in longer live-agent solve runs (AC-588).
- Release-sweep accounting hardened to prevent double-counting across concurrent sweep legs.

### Added

- Added a shared browser exploration contract and package-safe configuration surface across Python and TypeScript, including canonical schemas, validation helpers, secure `AUTOCONTEXT_BROWSER_*` defaults, and policy helpers.
- Added the TypeScript Chrome DevTools Protocol backend for browser exploration, including attach-only target discovery, websocket transport, policy-gated actions, and evidence artifacts.
- Added Python browser exploration integration for investigations and queued tasks, including policy-gated snapshot capture, prompt/evidence enrichment, and fail-closed task-runner wiring.
- Added a thin Python Chrome CDP browser backend with debugger-target discovery, evidence persistence, WebSocket transport, runtime factory, and policy-checked session actions.
- Added cross-runtime browser contract fixtures so Python and TypeScript validators stay in lockstep.
- Added TypeScript browser-context integration for investigations, queued tasks, and MCP queueing, including fail-closed navigation policy handling and artifact-backed browser evidence.

## [0.4.4] - 2026-04-20

### Added

- Added the production-traces contract and traffic-to-eval pipeline across Python and TypeScript, including cross-runtime schemas, emit/validate helpers, redaction, retention, dataset building, CLI/MCP surfaces, and golden integration flows.
- Added the TypeScript control-plane `model-routing` actuator plus the published `chooseModel` runtime helper for deterministic route, rollout, guardrail, fallback, and trace-integrated model selection.
- Added Python solve ergonomics for family overrides and improved classifier observability/fallback vocabulary for finance, schema-evolution, geopolitical simulation, and alignment-stress prompts.

### Fixed

- Hardened Python scenario design and solve paths around malformed designer responses, intent-drift retry feedback, mandatory calibration examples, structured quality thresholds, readable sample prompts, and schema/geopolitical simulate routing.
- Preserved the latest control-plane hardening while restacking the production-traces/model-routing foundation, including candidate artifact boundary validation and model-routing payload registration.

### Changed

- Python and TypeScript package metadata are bumped to `0.4.4`.

## [0.4.3] - 2026-04-17

### Fixed

- Hardened Pi-backed solve/runtime execution so Pi RPC waits for assistant completion, honors model/context-file options consistently, and solve runs enforce timeout budgets.
- Preserved generated-scenario family behavior across solve, export, TypeScript `new-scenario`, and `improve` flows, including empty-action family specs and improve calls without an initial output.
- Made custom scenario loading resilient and diagnosable: malformed specs no longer block registry discovery, spec-only directories surface actionable diagnostics, import-time missing files keep their real reason, and non-agent family specs can auto-materialize Python `scenario.py` sources.
- Normalized structured agent-task prompt payloads before validation and code generation, so JSON-like sample inputs, reference context, preparation instructions, and revision prompts no longer crash generated runtimes.

### Changed

- Python and TypeScript package metadata are bumped to `0.4.3`.

## [0.4.2] - 2026-04-16

### Fixed

- Preserved TypeScript workflow and custom-scenario semantics across broader scenario generation, including workflow compensation/side-effect metadata and camelCase final score weights.
- Hardened Python judge, improve, simulate, and list CLI flows around timeout overrides, fresh workspaces, provider overrides, rubric guardrails, and simulation-family routing.
- Added the Python `autoctx investigate` surface with generation fallbacks and kept its CLI implementation below the repository module-size gate.
- Restored Python `autoctx queue add --task-prompt ... --rubric ...` compatibility for prompt-backed queued tasks, including direct ad hoc queueing without a saved spec name.

### Changed

- Python and TypeScript package metadata are bumped to `0.4.2`.

## [0.4.1] - 2026-04-14

### Fixed

- Restored operator-loop escalation accounting when explicit escalation actions also mention clarification, so generated Python scenarios preserve both escalation and clarification signals.
- Preserved operator-loop family routing through Python solve creation and replay-safe feedback validation without violating the Pydantic serialization convention.
- Routed TypeScript `new-scenario` operator-loop requests through the dedicated family designer and allowed generated operator-loop scenarios to execute through the solve codegen path.
- Python and TypeScript package metadata are bumped to `0.4.1`.

## [0.4.0] - 2026-04-14

### Changed

- Refactored the TypeScript platform foundation, analytics/trace/training, and control-plane integration surfaces into thinner workflow modules while preserving CLI, MCP, and package parity.
- Hardened the extracted package-surface workflows around typed MCP tool boundaries, simulation dashboard report parsing, and deterministic simulation score normalization.
- Python and TypeScript package metadata are bumped to `0.4.0`.

## [0.3.7] - 2026-04-08

### Added

- TypeScript `autoctx campaign` CLI with create, status, list, add-mission, progress, pause, resume, and cancel subcommands, completing the CLI surface for CampaignManager (AC-533).
- Campaign API endpoints and MCP tools for multi-mission coordination with budget tracking and dependency graphs.

### Changed

- Standardized Anthropic credential loading around `ANTHROPIC_API_KEY` while keeping `AUTOCONTEXT_ANTHROPIC_API_KEY` as a compatibility alias across Python and TypeScript settings.
- Added optional role-scoped credential and endpoint overrides (`AUTOCONTEXT_{ROLE}_API_KEY`, `AUTOCONTEXT_{ROLE}_BASE_URL`) for `competitor`, `analyst`, `coach`, and `architect`, falling back to the global provider configuration when unset.

### Fixed

- Python `autoctx simulate` now resolves live generation through the effective architect-role runtime surface, so `AUTOCONTEXT_ARCHITECT_PROVIDER` and other role-routing overrides are honored instead of being bypassed by the raw client builder.
- Python simulation spec normalization now tolerates LLM-friendly action/spec shapes such as `postconditions`, nested criteria objects, and extra action-planning metadata without failing code generation.
- Structured simulation preconditions now preserve referenced action ids when LLM output includes both an `action` field and human-readable prose, so generated dependencies remain executable.
- Regenerating a custom scenario with the same name in one process now force-reloads the generated module so `solve` and creator validation do not reuse stale scenario classes from `sys.modules`.
- Pi-backed live flows now default to a 300 second timeout, reducing spurious failures in longer `solve` runs.
- Public docs now describe `operator-in-the-loop` as a runnable family and no longer contradict the executable tests.

## [0.3.6] - 2026-04-07

### Changed

- Hardened bootstrap, evidence, and privacy handling so environment snapshots redact shell paths correctly, rematerialized workspaces do not retain stale artifacts, and live prompt/evidence flows now wire the collected snapshot and evidence manifest into the real loop.
- Tightened scenario-generation safety in the TypeScript surface so `operator_loop` validation requires its real escalation/clarification hooks and spec auto-heal preserves punctuation-heavy precondition dependencies instead of dropping valid ordering.
- Improved evidence and security backstops by failing closed on TruffleHog execution errors and making the evidence workspace/MCP integration rely on a materialized runtime workspace instead of dead helper-only paths.
- Hardened blob-store backends so local keys cannot escape the configured root and Hugging Face bucket metadata/list/delete behavior remains accurate across fresh process boundaries.
- Python and TypeScript package metadata are bumped to `0.3.6`.

## [0.3.5] - 2026-04-06

### Changed

- Stabilized the post-`0.3.4` simulation path so operator-loop scenarios preserve behavioral-contract signals across multi-run, sweep, and replay flows instead of silently dropping them.
- Hardened plain-language simulation execution around explicit family detection, operator-loop contract enforcement, and shared CLI engine-result handling so incomplete runs surface consistently across Python and TypeScript surfaces.
- Tightened the simulation-engine implementation without regressing the repo module-size guardrail, including the compatibility shim needed by existing abstract-class filtering tests.
- Python and TypeScript package metadata are bumped to `0.3.5`.

## [0.3.4] - 2026-04-04

### Changed

- Added action-label and living-docs surfaces to the operator workflow, including reviewer-driven cleanup on the action-label taxonomy and living-docs maintenance path.
- Landed the TypeScript/Python parity tranche for session store and the full research package, keeping the rebased cross-surface runtime behavior aligned on current `main`.
- Folded in the `pi-autocontext` polish follow-up so the published Pi package line reflects the renamed extension and its best-practices cleanup.
- Python and TypeScript package metadata are bumped to `0.3.4`.

## [0.3.3] - 2026-04-03

### Changed

- Expanded the research surface with validated domain contracts, runtime gating, persistence hardening, and better evaluation wiring for briefs, prompts, and adapters.
- Hardened Python and TypeScript operator-control surfaces around terminal lifecycle transitions, remote approvals, progress digests, and agentOS session/runtime error handling.
- Improved SQLite bootstrap and migration compatibility so packaged installs and fresh databases stay aligned with the live generation schema.
- Expanded the TypeScript provider compatibility surface with env-driven config for `gemini`, `mistral`, `groq`, `openrouter`, and `azure-openai`, and synced the public provider docs/tests to match.
- Python and TypeScript package metadata are bumped to `0.3.3`.

## [0.3.2] - 2026-04-02

### Changed

- Completed the TypeScript session-runtime parity pass across lifecycle management, coordinator state transitions, supervision, context pressure, remote approvals, progress digests, memory consolidation, and skill registry behavior.
- Hardened the TypeScript operator control plane so terminal session and worker states stay terminal, remote approvals require connected controllers, and redirected work remains visible in progress summaries.
- Python and TypeScript package metadata are bumped to `0.3.2`.

## [0.3.1] - 2026-04-01

### Changed

- Python package publishing now uses the canonical PyPI name `autocontext` instead of `autoctx`.
- Public install docs now reflect the package split accurately: PyPI is `autocontext`, while npm remains `autoctx`.
- Python and TypeScript package metadata are bumped to `0.3.1`.

## [0.3.0] - 2026-03-29

### Added

#### Commands

- **`autoctx simulate`** — plain-language multi-variable simulation with sweeps, replay, compare, and export.
- **`autoctx investigate`** — evidence-driven diagnosis with hypotheses, confidence scoring, and unknowns.
- **`autoctx analyze`** — interpret and compare runs, simulations, investigations, and missions.
- **`autoctx train`** — train distilled models from curated datasets with backend selection.
- **Python `autoctx simulate`** — full parity with the TypeScript surface: run, replay, compare, and export.

#### Scenarios

- All 11 scenario families now fully executable in TypeScript (was 2/11) via secure-exec V8 isolate codegen.
- `operator_loop` is now a fully runnable family in both packages.
- Unified family classifier: all families reachable through the CLI.
- Spec auto-heal: codegen failures trigger automatic recovery.
- Scenario revision flow: refine created scenarios with feedback.
- Deep execution validation: generated code is executed and verified before registration.
- Three scenario templates: content-generation, prompt-optimization, and rag-accuracy.
- `new-scenario` CLI materializes runnable artifacts to disk.
- Scenario parity matrix documents Python/TypeScript surface coverage.

#### Missions & Campaigns

- Adaptive mission execution: LLM-driven goal decomposition and step planning replaces generic bookkeeping.
- Campaign abstraction: coordinate multiple missions under long-term goals with budget tracking and dependencies.
- Mission-simulation integration: missions invoke simulations as planning tools.

#### Trace Pipeline

- Open public trace schema v1.0.0: versioned interchange format for coding agent traces.
- Sensitive-data detection and redaction with policy-backed actions.
- Privacy-aware trace export workflow: redact, validate, manifest, and attestation.
- Publishing connectors for local JSONL, GitHub Gist, and Hugging Face.
- Trace-to-model data plane with `DatasetCurator` and `DataPlane`.
- Repo-local dataset discovery: scan repo trees and convert JSONL, JSON, CSV, and markdown into ShareGPT-style records.
- Curated distillation dataset pipeline with gate filtering, top-quartile selection, family filtering, and failure-example policy.

#### Training & Distillation

- Base model selection maps scenario families to training modes (from-scratch, LoRA, and full fine-tune).
- Training backend abstraction with MLX and CUDA plus an injectable `TrainingExecutor` hook.
- Prompt alignment ensures distilled models match runtime invocation.
- Candidate-shadow-active promotion lifecycle with configurable quantitative gates and rollback.

### Changed

- Consolidated operator UI: the Python `serve` and `tui` surfaces are API/WebSocket-first, while interactive terminal UI remains available through the TypeScript client surfaces.
- Richer sweep DSL: categorical sweeps, logarithmic scales, sweep file loading, and named presets.

### Fixed

- Trace pipeline audit: expanded redaction patterns, ISO 8601 timestamp validation, explicit role mapping, export warnings, and Hugging Face format fixes.
- Distillation audit: training executor hook, base model validation, CSV parser edge cases, silent catches now surfaced as warnings, and end-to-end integration coverage.

## [0.2.4] - 2026-03-26

### Added

- Session notebook context now flows into runtime prompts and cockpit views for active runs.
- World-state abstractions now support stateful scenario families and workflow-style scenarios.

### Changed

- Agent-task scaffolding and execution now use separate phased budgets.
- Operator-loop scenarios remain available as typed family metadata, but executable operator-loop scaffolding has been removed so the harness no longer bakes in escalation-specific runtime behavior.
- Public repo docs now include a docs landing page, package-selection guidance, an analytics/adoption guide, a release checklist, and copy-paste integration examples for CLI, MCP, Python SDK, and TypeScript usage.

### Fixed

- Python package fallback version metadata now matches the published `0.2.0` package version.

## [0.2.0] - 2026-03-15

### Added

- Initial public release with Python and TypeScript packages.
- Generation loop with Elo-based progression gating.
- Agent roles: competitor, analyst, coach, architect, and curator.
- Pluggable scenarios including `grid_ctf`, `othello`, and the custom creation pipeline.
- LLM judge with multi-sample evaluation.
- Task runner daemon with improvement loops.
- MCP server with tool implementations.
- FastAPI dashboard with WebSocket events.
- CLI via Typer (Python) and `parseArgs` (TypeScript).

[Unreleased]: https://github.com/greyhaven-ai/autocontext/compare/py-v0.5.0...HEAD
[0.5.0]: https://github.com/greyhaven-ai/autocontext/compare/py-v0.4.9...py-v0.5.0
[0.4.9]: https://github.com/greyhaven-ai/autocontext/compare/py-v0.4.8...py-v0.4.9
[0.4.8]: https://github.com/greyhaven-ai/autocontext/compare/py-v0.4.7...py-v0.4.8
[0.4.7]: https://github.com/greyhaven-ai/autocontext/compare/py-v0.4.6...py-v0.4.7
[0.4.6]: https://github.com/greyhaven-ai/autocontext/compare/py-v0.4.5...py-v0.4.6
[0.4.5]: https://github.com/greyhaven-ai/autocontext/compare/py-v0.4.4...py-v0.4.5
[0.4.4]: https://github.com/greyhaven-ai/autocontext/compare/py-v0.4.3...py-v0.4.4
[0.4.3]: https://github.com/greyhaven-ai/autocontext/compare/py-v0.4.2...py-v0.4.3
[0.4.2]: https://github.com/greyhaven-ai/autocontext/compare/py-v0.4.1...py-v0.4.2
[0.4.1]: https://github.com/greyhaven-ai/autocontext/compare/py-v0.4.0...py-v0.4.1
[0.4.0]: https://github.com/greyhaven-ai/autocontext/compare/py-v0.3.7...py-v0.4.0
[0.3.7]: https://github.com/greyhaven-ai/autocontext/compare/py-v0.3.6...py-v0.3.7
[0.3.6]: https://github.com/greyhaven-ai/autocontext/compare/py-v0.3.5...py-v0.3.6
[0.3.5]: https://github.com/greyhaven-ai/autocontext/compare/py-v0.3.4...py-v0.3.5
[0.3.4]: https://github.com/greyhaven-ai/autocontext/compare/py-v0.3.3...py-v0.3.4
[0.3.3]: https://github.com/greyhaven-ai/autocontext/compare/py-v0.3.2...py-v0.3.3
[0.3.2]: https://github.com/greyhaven-ai/autocontext/compare/py-v0.3.1...py-v0.3.2
[0.3.1]: https://github.com/greyhaven-ai/autocontext/compare/py-v0.3.0...py-v0.3.1
[0.3.0]: https://github.com/greyhaven-ai/autocontext/compare/py-v0.2.4...py-v0.3.0
[0.2.4]: https://github.com/greyhaven-ai/autocontext/compare/v0.2.0...py-v0.2.4
[0.2.0]: https://github.com/greyhaven-ai/autocontext/releases/tag/v0.2.0
