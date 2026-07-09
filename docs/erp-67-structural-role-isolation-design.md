# Structural role-message isolation for untrusted content — design (ERP-67)

**Status (updated):** Stages 1–3 implemented and merged (behind the
`structural_role_isolation` flag, **default off**). Stage 4 (default-on) is
**blocked** — see "Stage 4" below. This document began as a design/staged plan;
the sections below now mix the original plan with the as-built state, noted
where they differ. Follow-up to **ERP-59** (shipped: in-band guardrail preamble +
`[BEGIN/END UNTRUSTED REFERENCE]` fencing in `build_prompt_bundle`).

> **As-built note on the call path (below):** the original text says
> `SubagentTask` has no system/user seam. That was the pre-Stage-2 state. As
> built, `SubagentTask` now carries an optional `system` and `SubagentRuntime`
> routes it via `generate_multiturn` for role-capable clients (Stage 2); the
> orchestrator resolves each role's turn via `resolve_role_turn` on both the
> direct and pipeline paths (Stage 2b/2c).

## Problem

ERP-59 is the strongest defence available **without** touching the LLM
transport: it prepends a data-not-instructions guardrail and fences the
attacker-influenceable fields (playbook, coach hints, dead-ends). But the fence
is still just text inside one prompt string. Structural isolation — untrusted
content delivered in a **separate message/role** from the operator
instructions — is the stronger defence ERP-59 explicitly deferred.

## The call path today (why this is cross-cutting)

Traced from the role prompt to the model:

```
build_prompt_bundle()                      prompts/templates.py
  → PromptBundle(competitor/analyst/…)     one FLAT string per role
                                           (ERP-59 guardrail + fences baked in)
  → orchestrator reads prompts.<role>      agents/orchestrator.py
  → RoleRunner.run(prompt: str)            agents/{competitor,analyst,coach,architect}.py
  → SubagentTask(prompt=<one string>)      harness/core/subagent.py
  → SubagentRuntime.run_task()             → client.generate(prompt=<one string>)
  → LanguageModelClient.generate(prompt)   ~15 concrete impls (see below)
  → model
```

Two structural facts:

1. **The main role-execution path has no system/user seam at all.**
   `SubagentTask` carries a single `prompt: str`; `SubagentRuntime.run_task`
   calls `client.generate(prompt=…)`. There is nowhere to put "untrusted data
   as a distinct turn" without changing this transport.
2. **A role seam exists and several adapters already honour it, but the main
   path does not use it.** `LanguageModelClient` declares
   `generate_multiturn(system, messages=[…])`, and the separate
   `LLMProvider.complete(system_prompt, user_prompt)` abstraction
   (providers/base.py) exists. Some concrete adapters already send the seam
   structurally: `AnthropicClient.generate_multiturn` passes `system=` and
   `messages=` straight to the API, and `AgentSdkClient` sets
   `options.system_prompt` separately from the user prompt.
   `LLMProvider.complete` is on the role-execution path too (via
   `ProviderBridgeClient`), not only judging. The real gap is narrower than
   "no seam anywhere": the single-`prompt` `SubagentTask` path never fills the
   system turn, and the flattening adapters collapse it back to one string
   (`OpenClawClient.generate_multiturn` yields `system + "\n\n" + user_parts`;
   `cli_role_runtime._llm_fn` yields `f"{system}\n\n{user}"`; and
   `ProviderBridgeClient.generate` currently calls `complete(system_prompt="")`).
   Stage 2 therefore populates these existing role-capable paths rather than
   inventing a new transport.

### Blast radius

`generate`/`generate_multiturn` is implemented by ~15 clients:

- **runtimes/**: `direct_api`, `claude_cli`, `hermes_cli`, `codex_cli`,
  `pi_cli`, `pi_rpc`, `base`
- **agents/**: `AnthropicClient`, `MLXClient`, `MLXLMClient`,
  `DeferredMLXClient`, `DeterministicDevClient`, `PanelLanguageModelClient`,
  `SftTorchClient`, `agent_sdk_client`, `provider_bridge`
- **openclaw/**: `OpenClawClient`
- **extensions/llm.py**, plus recording/session wrappers

Backends split into three tiers by how much of the role seam they can carry:

- **Full message roles**: **Anthropic / OpenAI-style APIs** (`AnthropicClient`,
  `direct_api`) take a `system` param plus `messages[]`, and `AgentSdkClient`
  takes a separate `system_prompt`. These carry the trusted-system vs
  untrusted-user split cleanly.
- **System-prompt split only**: `ClaudeCLIRuntime.generate(system=…)` threads
  `--system-prompt` separately from the user prompt, so it preserves the key
  trusted/untrusted seam even though it cannot replay arbitrary message
  history. Partial support, not fallback-only.
- **Single prompt**: `pi_cli` and `hermes_cli` flatten `f"{system}\n\n{prompt}"`,
  `codex_cli` drops `system` altogether, and **OpenClaw** takes one string.
  These must fall back to the ERP-59 in-band fence.

One wiring caveat: even where the runtime supports a system split,
`RuntimeBridgeClient.generate` currently calls `runtime.generate(prompt)`
without threading `system`, so claude_cli's capability is not exercised on the
role path yet. That wiring is part of the Stage 2 work, not a missing backend
feature. So this remains "isolate where the backend supports it, fence
everywhere else", with more backends in the supported column than a first pass
suggests.

## Contract

Extend the prompt contract so each role produces **two parts** instead of one
flat string:

- `system` — operator instructions: scenario rules, strategy interface,
  evaluation criteria, role task, constraints, the ERP-59 guardrail. Trusted.
- `untrusted_reference` — the fenced attacker-influenceable blocks (playbook,
  coach hints, dead-ends) and any future document-derived context.

Transport rule:

- Role-capable backends → `system` in the system turn, `untrusted_reference`
  (still fenced, belt-and-suspenders) as a **user** turn.
- Single-prompt backends → concatenate exactly as today (ERP-59 fence is the
  defence). Behaviour byte-identical to current output.

## Staged, feature-flagged rollout

Each stage ships independently, is reversible, and keeps the full suite green.

**Stage 1 — split without changing transport (behaviour-preserving).**
`build_prompt_bundle` also returns the (system, untrusted_reference) split per
role (e.g. a `PromptParts` alongside the existing flat `PromptBundle`). At the
boundary, concatenate the two parts in the current order so the emitted prompt
is byte-identical. Add tests asserting the split is correct (untrusted fields
land only in `untrusted_reference`; guardrail in `system`). No transport change,
no provider change — lands the separation of concerns safely.

**Stage 2 — thread messages through the transport, behind a flag.**
Add `system` + `messages` to `SubagentTask`; `SubagentRuntime.run_task` prefers
`generate_multiturn` when `settings.structural_role_isolation` is on and the
client advertises role support (a capability flag / `supports_roles`
attribute), else falls back to today's `generate(prompt=…)`. Implement real
role emission in the full-message-role clients first (`AnthropicClient`,
`agent_sdk_client`, direct_api), then thread `system` through
`RuntimeBridgeClient.generate` so `ClaudeCLIRuntime`'s existing
`--system-prompt` split (partial support) is exercised on the role path. The
remaining single-prompt backends (`pi_cli`, `hermes_cli`, `codex_cli`,
OpenClaw) keep flattening and lean on the ERP-59 in-band fence (documented).

**Stage 3 — adversarial eval.**
Extend `tests/test_prompt_injection_boundary.py` with structural variants:
role-reassignment, fake-system-prompt, tool-call injection. Under the flag,
assert the injected text appears **only** in the user turn and never in the
system turn, and does not change the agent's actions.

**Stage 4 — flip the default** — **NOT done; blocked.** The default stays
`False`. Two hard prerequisites must land first:

**Prerequisite A — complete the trust classification (correctness bug).** _Done._
The split originally routed only the ERP-59 fenced fields (playbook / coach hints
/ dead-ends) to `untrusted_reference` and left everything else in `system` — but
much of that was model-, user-, or document-derived (prior analyst output, coach
lessons, architect-generated tool context, session reports, evidence manifests,
editable notebooks, task observation, environment snapshot, trajectory, etc.),
i.e. attacker-influenceable second-order injection. Enabling isolation would have
_promoted_ it to system authority — strictly worse. The split now keeps **only
operator-authored** text in `system` (the system-turn guardrail, scenario rules,
strategy interface, evaluation criteria, role task, role constraints, hint
policy, simplicity guidance) and routes **everything else** to the untrusted user
turn. Adversarial sentinel tests cover each shared derived component and the
role-specific ones (`tests/test_prompt_parts_isolation.py`). `flat` is unchanged
and byte-identical. So isolation-on is now a net security win; only the soak
below gates default-on.

**Prerequisite B — a capable-backend soak with an objective gate.** CI's offline
`DeterministicDevClient` is incapable, so the suite exercises the flat path and
cannot detect a quality shift; validation must run on a real capable backend.
Gate:

- **Setup:** one representative scenario set; **fixed** provider + model +
  temperature (0.0) + generation budget; **≥ 20 paired seeds** (same seed list
  for on and off).
- **Commands:** run the standard eval loop twice per seed —
  `AUTOCONTEXT_STRUCTURAL_ROLE_ISOLATION=false` then `=true` — capturing the
  existing tournament score / evaluation summary per run.
- **Acceptance:** paired per-seed score deltas; require the mean delta within
  **±2%** of the off baseline and no statistically-significant regression (paired
  t-test / Wilcoxon, p > 0.05) after accounting for run-to-run noise measured
  from off-vs-off repeats.
- **Behavioural:** additionally seed an injected scenario and confirm the agent's
  parsed **actions/strategy** are unchanged vs the clean run on the capable
  backend (Stage 3 covers message _placement_ only, not behaviour).
- **Record:** persist the paired results + verdict to a checked-in report (e.g.
  `docs/erp-67-stage4-soak-<date>.md`) and link it from the flip PR.

**Rollback / escape hatch (real surface).** There is no user config-file loader
for this field; `load_settings()` reads env + presets. To toggle:
`AUTOCONTEXT_STRUCTURAL_ROLE_ISOLATION=true|false`. It takes effect on the **next
settings load**, so a running worker/server must be **restarted** to pick it up.
When the default is eventually flipped, add the change + this env var to
`.env.example`, `autocontext/README.md`, and `CHANGELOG.md`.

## Risks / notes

- **Prompt-shape change alters model behaviour.** Splitting system/user can
  shift outputs even with identical content. Stage 1 is byte-identical on
  purpose; Stage 2+ must be flagged and evaluated (score parity) before default.
- **Capability detection over hardcoding.** Gate role emission on a per-client
  capability flag, not a name allowlist, so new runtimes are correct by default
  (fall back to fence).
- **Recording/session wrappers** (`provider_bridge`, runtime-session recording)
  must forward `generate_multiturn` faithfully or the flag silently no-ops.
- **~hundreds of tests** construct `SubagentTask`/call `generate`; keep the
  single-prompt path as the default until Stage 4 to avoid a mass test rewrite.

## Recommendation

Do **Stage 1 now** (cheap, behaviour-preserving, unblocks the rest) as its own
PR. Gate Stages 2–4 behind `structural_role_isolation` and land them
incrementally with score-parity checks. ERP-59's in-band fence remains the
belt; structural isolation is the belt-and-braces for role-capable backends.
