# ambient trainer design

Date: 2026-07-03
Status: approved in brainstorming; pending written review
Working name: `autoctx ambient`

## Purpose

A resident data-plane daemon for Linux GPU boxes that learns continuously from
the reasoning traces flowing through it: it ingests traces, curates datasets,
proposes and executes fine-tunes, evaluates candidates against a frozen anchor,
and promotes winners into live serving so autocontext's own roles (and any
user workload) progressively run on locally-trained models.

It ships as a product feature of autocontext. The first dogfood is recursive:
training local replacements for autocontext's generator roles on our own GPU
box, with the cost and quality delta versus frontier APIs as the headline
telemetry.

## Decisions of record

1. **Purpose**: product feature, dogfooded via recursive local role models.
2. **Intent**: a declarative charter file is the daemon's only policy input;
   an interview wizard bootstraps it; an autonomy dial governs how much the
   daemon does without approval; an advisor proposes charter amendments.
3. **Data scope is tiered**: the OSS package ingests autocontext-native runs,
   the OTel/production-traces feed, and an LLM proxy tap. The hosted tier
   (boxes we provision) adds a full-box collector (shell, editor, all LLM
   calls). The hairiest privacy surface stays in the deployment where the
   consent story is controlled.
4. **Topology**: data-plane worker daemon on the GPU box plus a control
   surface abstraction with two backends: local (charter file, CLI/TUI
   approvals) for OSS/standalone, and the autowork control plane for managed
   fleets. The engine stays a dumb worker; policy authority lives in the
   control surface.
5. **Architecture**: pipeline daemon (approach A): five loosely-coupled
   stages around a durable queue. Longevity commitment: every stage runs both
   resident and one-shot (`autoctx ambient once <stage>`), so the system can
   degrade into a cron-over-stages model without a rewrite and can later split
   stages across machines by re-pointing queue endpoints.
6. **V1 bar**: the full closed loop on our box: interview to charter, ingest,
   advisor proposal, train, frozen-anchor eval, promotion, vLLM serving, with
   autocontext roles actually using the promoted model.
7. **Bias posture**: asymmetric trainability, frozen-anchor promotion,
   provenance quarantine, drift canaries, and a minimum frontier-traffic
   fraction. These are guardrails with a non-removable floor, not defaults.

## System overview

```
sources -> [Ingest] -> trace store -> [Curate] -> dataset store -> [Train] -> checkpoints
                |                          |                            |
                v                          v                            v
            redaction                  [Advise] -> proposals   [Evaluate/Promote/Serve]
                                           ^                            |
                                           |                            v
                                        charter (policy) <------ vLLM + model registry
```

One Python process, five stages, one durable SQLite-backed work queue (the
existing task-queue idiom). Python owns the daemon because it owns the
training backends; the TypeScript side receives contract parity for charter,
registry, and telemetry types per repo convention (contracts, not execution).

### Stage: Ingest

Pluggable trace sources, each opt-in per charter with a consent flag and a
redaction profile:

- autocontext-native: the loop's own runs, generations, matches, judgments
  (richest labels; already structured).
- OTel feed: anything shipping traces via the existing otel-bridge and
  production-traces SDK (other agents, Claude Code hooks, instrumented apps).
- LLM proxy tap (plan 3): the daemon exposes an OpenAI/Anthropic-compatible gateway
  endpoint; any tool pointed at it gets traced. Lowest-friction third-party
  capture; lacks tool/execution context by nature.
- full-box collector (hosted tier only, v2): shell sessions, editor activity,
  all LLM calls on the box.

Redaction (existing redactor) applies at ingest, before anything is persisted
as training-eligible. Every record carries source, provenance, and lineage
tags (`produced_by: frontier | finetune:<lineage-id>`). The trace store is
append-only with retention driven by the charter's disk budget.

### Stage: Curate

Continuous dataset construction on the existing dataset adapters, manifests,
and curation workflows. Maintains one dataset manifest per charter target with
quality stats and freshness. Two guardrails are enforced here, mechanically:

- **Eligibility filter (asymmetric trainability)**: a dataset for an
  evaluative role (judge, curator, coach) may only include records whose
  labels are externally anchored: human feedback, objective verifier
  outcomes, or frontier-model annotations. A trained judge's own verdicts are
  never eligible labels for a judge target. Generator-role datasets are
  unrestricted beyond quarantine.
- **Provenance quarantine**: records produced by fine-tune lineage N are
  excluded (or down-weighted per charter) from lineage N+1's training set.
  Drift cannot compound within a lineage.

### Stage: Advise

The hermes advisor generalized. Watches curation stats (volume, quality,
pass-rates, novelty per task family) and emits **charter proposals**:
structured charter diffs such as "4,100 Lean-verifier traces at 92% pass rate;
propose a 3B prover target, estimated 6 GPU-hours." Proposals flow to the
control surface; approval applies the diff. The charter's evolution is
therefore an auditable sequence of applied diffs.

### Stage: Train

Executes charter targets through the existing backends (CUDA/TRL, MLX) when a
target's minimum-dataset threshold and budget window trip. Methods: SFT
distillation (v1), RLVR (flagged experimental in v1, automated in v2).
GPU-aware scheduling: jobs declare VRAM needs from the existing scale
profiles; the scheduler refuses to start a train that would evict serving
unless the charter's priority says training wins.

### Stage: Evaluate / Promote / Serve

- **Frozen-anchor evaluation**: candidates are judged on a held-out suite by
  the anchor pinned in the charter (a frontier API model or the frozen base),
  versioned in the eval record. Promotion requires beating the incumbent
  under the anchor, never under a trained sibling.
- **Drift canaries**: a fixed bias-probe suite (the judge bias-probe and
  rubric-calibration machinery) runs at every promotion; judge-score
  inflation relative to the anchor must stay within tolerance.
- **Promotion**: existing promotion-engine and registry workflows record the
  checkpoint, lineage, eval evidence, and role binding.
- **Serving**: a model server manager supervises vLLM (load, health-check,
  hot-swap, rollback). Role binding lives in the registry ("competitor@
  grid_ctf resolves to lineage-7 v3"), not in scenario code; the existing
  vllm provider path routes role traffic. The previous checkpoint stays warm
  through a probation window (charter-defined: N runs or M days within
  quality-delta and error-rate bounds); rollback is one registry flip.
- **Per-role live-serving bridge (opt-in, AC-893)**: ambient candidates are
  slotted in the registry by target name (so activation never cross-demotes a
  sibling, AC-884), but the generation loop resolves a local model by the real
  scenario. The bridge is a serving manifest that the promote stage writes on
  activation ((real scenario, role) -> target/artifact/backend) and the serving
  resolver reads before its scenario-keyed lookup. It is off by default. To turn
  it on, set the SAME manifest path for BOTH sides via
  `AUTOCONTEXT_AMBIENT_SERVING_MANIFEST_PATH`: the ambient daemon (which runs the
  promote stage) and the generation-loop process (which resolves the served
  client) must point at one shared file, or the bridge writes and reads never
  meet. A bare-role selector (`competitor`) serves every scenario; a scoped
  selector (`competitor@grid_ctf`) serves only that scenario.
- **Frontier fraction**: the router keeps the charter's minimum share of role
  traffic on frontier APIs so the reference distribution never dries up and a
  live comparison population always exists.

## The charter

`ambient-charter.yaml`, schema-validated, bootstrapped by `autoctx ambient
init` (an interview wizard covering goals, sources, model families, budgets,
autonomy). The charter is the daemon's only policy input; there are no other
behavior flags. Schema groups:

- **identity/tier**: deployment tier (`oss` | `hosted-box`), control surface
  binding (`local` | `autowork`).
- **sources[]**: enabled trace sources, consent flags, redaction profiles.
- **targets[]**: role or task-family selector, base model and size, method
  (`sft-distill` | `rlvr-experimental`), data filters, minimum-dataset
  thresholds, eval suite, promotion criteria, optional per-target autonomy
  override.
- **budgets**: GPU-hours per window, disk quotas, serving-versus-training
  priority.
- **autonomy**: the dial: `propose` (advisor suggests, human approves
  everything) | `train` (auto-train, human approves promotion) | `full`
  (auto-promote within budgets and eval gates).
- **guardrails**: asymmetric trainability, frozen-anchor promotion,
  provenance quarantine, drift canaries, minimum frontier fraction. Values
  are tunable above a floor; the floor is not removable through the charter.

## Control surface

One interface: charter read/write and diff application, proposal approval,
telemetry and event subscription, manual stage triggers, promotion sign-off.
Two backends: local (file plus CLI/TUI: `autoctx ambient status | proposals |
approve | history`) and the autowork control plane (fleet view, RBAC, audit).
V1 ships local; the interface is stubbed against the autowork shape from day
one so v2 is a backend, not a redesign.

## Error handling and operations

- Per-stage auto-pause breaker: N consecutive failures pauses that stage
  only, alerts the control surface, leaves the rest running.
- Idempotent stage work against the durable queue; run-manifest locks prevent
  double-training after crash-resume.
- Disk pressure triggers retention compaction before it pauses ingest.
- systemd supervision, health endpoints, `autoctx ambient status` parity
  between standalone and fleet views.
- Full reconstructibility: everything autonomous is explainable from the
  event stream plus the charter-diff history.

## Testing

- Unit: charter schema and policy engine (autonomy dial, budget windows,
  guardrail floors) as pure functions; eligibility filter with adversarial
  fixtures (self-labeled judge records must be rejected; quarantine
  violations must fail curation).
- Integration: each stage one-shot against fixture stores.
- End-to-end: a "miniature ambient" CI test using the deterministic provider
  and a tiny CPU/MLX-trainable model running ingest through serve in minutes.
- Guardrail regressions: a synthetic drifting judge must trip the canary.
- Contract parity tests for the TS-side charter/registry/telemetry types.
- The full ts-test and pytest CI gates apply as with any subsystem.

## V1 slice

In scope: the daemon with all five stages; charter, interview, autonomy dial;
autocontext-native, OTel, and proxy-tap ingestion; SFT-distillation targets
for generator roles; frozen-anchor eval, promotion, vLLM serving with
rollback and probation; local control surface; telemetry events; the
miniature-ambient CI test.

Out of scope (v2+): the hosted full-box collector; the autowork control-plane
backend (interface stubbed now); multi-box fleets; evaluative-role training;
automated RLVR targets.

Acceptance: on our GPU box, `autoctx ambient init` through to at least one
generator role served by a locally-trained model that beat baseline under the
anchor, with cost and quality deltas visible in telemetry.

## Risks

- Residency reliability (OOM, driver faults, disk pressure): mitigated by
  breakers, idempotency, one-shot degradation, systemd; not by design novelty.
- Reward hacking and drift despite guardrails: the frontier fraction and
  anchor pinning keep an external reference; canaries are regression-tested.
- GPU contention between serving and training: charter priority plus
  VRAM-aware scheduling; worst case is delayed training, never broken serving.
- Dataset quality plateaus (the cap-sets representation-ceiling lesson):
  the advisor reports per-target marginal-gain trends so a stalling target is
  visible rather than silently consuming budget.

## Inspirations borrowed

From ColeMurray/background-agents: durable per-session event streams,
auto-pause after consecutive failures, lifecycle scripts, control-plane and
data-plane separation. From our own history: verifier-driven training (RLVR
at 3B+), "compiles is not proved" oracle discipline, provenance tagging in
the dataset adapters, and the control-plane thesis (engine stays a dumb
worker).
