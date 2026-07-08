# Structural role-message isolation for untrusted content — design (ERP-67)

**Status:** design / staged plan. No behavioural code in this PR — it records
the real call path, the contract, and a staged, feature-flagged rollout for a
large cross-cutting change. Follow-up to **ERP-59** (shipped: in-band guardrail
preamble + `[BEGIN/END UNTRUSTED REFERENCE]` fencing in `build_prompt_bundle`).

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
2. **A role seam exists but is unused on this path.** `LanguageModelClient`
   already declares `generate_multiturn(system, messages=[…])`, and the
   separate `LLMProvider.complete(system_prompt, user_prompt)` abstraction
   (providers/base.py) exists — but it's used for _judging_, not role
   execution, and every concrete adapter flattens `generate_multiturn` back
   into a single string anyway (e.g. `OpenClawClient.generate_multiturn` →
   `system + "\n\n" + user_parts`; `cli_role_runtime._llm_fn` →
   `f"{system}\n\n{user}"`).

### Blast radius

`generate`/`generate_multiturn` is implemented by ~15 clients:

- **runtimes/**: `direct_api`, `claude_cli`, `hermes_cli`, `codex_cli`,
  `pi_cli`, `pi_rpc`, `base`
- **agents/**: `AnthropicClient`, `MLXClient`, `MLXLMClient`,
  `DeferredMLXClient`, `DeterministicDevClient`, `PanelLanguageModelClient`,
  `SftTorchClient`, `agent_sdk_client`, `provider_bridge`
- **openclaw/**: `OpenClawClient`
- **extensions/llm.py**, plus recording/session wrappers

Only some backends can honour real message roles: **Anthropic / OpenAI-style
APIs** take a `system` param + `messages[]`; **CLI runtimes** (claude_cli,
codex_cli, pi_cli, hermes) and **OpenClaw** take a single prompt string and
physically cannot separate roles — they must fall back to the ERP-59 in-band
fence. So this is "isolate where the backend supports it, fence everywhere
else", not a universal win.

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
role emission in the role-capable clients first (`AnthropicClient`,
`agent_sdk_client`, direct_api). CLI/OpenClaw keep flattening (documented).

**Stage 3 — adversarial eval.**
Extend `tests/test_prompt_injection_boundary.py` with structural variants:
role-reassignment, fake-system-prompt, tool-call injection. Under the flag,
assert the injected text appears **only** in the user turn and never in the
system turn, and does not change the agent's actions.

**Stage 4 — flip the default** once Stage 2/3 have soaked, keeping the flag as
an escape hatch.

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
