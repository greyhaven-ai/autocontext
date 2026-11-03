# Adaptive Qwen editing fixtures — milestone 7

This CPU-only example connects [bounded editing sessions](../qwen_sessions/README.md)
to autocontext's existing provider/schema and refinement-prompt interfaces. It
proposes edits from verified practice feedback, evaluates and selects candidates,
then applies a separate confirmation gate before the final fixture audit.
Providers and probes use pinned literal catalogs. No model, GPU, or generated
program executes; the weight arm still edits a synthetic descriptor.

From the repository's `autocontext/` directory:

```bash
uv sync --locked --group dev
uv run python ../examples/weight_evolution/qwen_adaptive/run.py validate
uv run python ../examples/weight_evolution/qwen_adaptive/run.py run-fixtures \
  --output ./qwen-adaptive-veto
# Exact resume verifies saved work without more generation/probe dispatches.
uv run python ../examples/weight_evolution/qwen_adaptive/run.py run-fixtures \
  --output ./qwen-adaptive-veto
```

The checked-in example deliberately produces a confirmation veto for D. Its
successful behavior is to retain all evidence and **withhold the final audit**.
There is no live execution switch, provider registry lookup, API request,
model/data download, or hardware provisioning.

## Practice-driven search

All C/W/D arms share the same baseline, three practice questions, scorer,
three proposal rounds, per-operation reservations, and finite total limits.
Each arm starts with a complete baseline practice probe. Every round then:

1. Reserves a proposal inspection in the durable M6 journal.
2. Builds `build_refinement_prompt` from the current state and its verified
   practice feedback, then calls the normal `LLMProvider.complete` interface
   with `OutputSchema`.
3. Validates the returned JSON locally, including the allowed edit surface,
   regardless of the provider's `constrained` flag.
4. Applies the typed edit and probes the full practice cohort.

Only the pinned `OfflineFeedbackProvider` is accepted. Its response is addressed
by an exact digest of candidate state, practice prompts, and feedback, rather
than a call counter. Altering feedback changes which response is available.
The fixture therefore exercises adaptive control flow while making no claim
that an LLM discovered an improvement. Provider model, constraint/termination
metadata, expanded input bytes, and raw output bytes are retained.

Adapters never receive gold answers, confirmation rows, final-audit rows, or
their outcomes. Current-candidate practice scores and response hashes provide
feedback. Invalid/truncated proposals and known provider/probe failures consume
their reservations and remain distinct from valid answers that score zero.
Ambiguous dispatches block progress and automatic retry.

Selection ranks changed candidates with complete practice probes by total
correct answers, breaking ties by earliest round. It freezes this choice before
finalization. An earlier winner is restored by a charged edit and always gets
a final practice verification probe before export. The controller cannot
silently substitute a historical state or fall back to another candidate after
confirmation. No eligible candidate, failed restoration, or changed verification
scores ends the search without a usable export.

The example's literal trajectory is baseline 1/3, first candidate 2/3, then a
second candidate at 3/3. The third round regresses C, fails W's proposal, and
fails D's probe. All select round two; C and D restore it before sealing.

## Equal caps and durable costs

Every arm permits at most 12 operations, 122,880 editor-input bytes, 49,152
editor-output bytes, 20,480 probe-input bytes, 20,480 probe-output bytes, and
12 simulated seconds. The total covers the worst case: baseline probe, three
full rounds, one restoration edit, and final verification. Expanded provider
input is checked before `complete`; raw provider output and the adapter result
must also fit their reservations.

These are conservative fixture byte reservations and simulated time, not model
tokens, measured GPU time, or a hard wall-clock timeout. Editor/probe counters
overlap by design. M5 search cost sums those four reserved byte counters.
The example charges C/D 12 calls and 212,992 units each; W uses 9 calls and
167,936 units because its final proposal fails before an edit/probe. Capacity
is matched; actual completed work and observed costs are reported separately.

Restart verifies the frozen policy, source identities, full M6 history, selected
candidate, terminal markers, and sealed export. Replaying an exact completed
operation never redispatches it or resets its charge. Preserve incomplete or
ambiguous evidence for reconciliation. A new directory is a new experiment,
not continuation of a previous budget or permission to reuse a holdout.

## Separate confirmation and final audit

All three searches must finish and seal before the coordinator commits an
all-arm selection file. The confirmation module verifies its linked search
manifests, original selections, restored candidates, costs, and M6 exports.
The confirmation baseline and scorer must also match the search contracts.

One immutable confirmation claim binds the selection, data role, plan, literal
responses, policy, and source identities before grading. It uses the existing
five-condition evaluator on three separate toy confirmation questions, with
matched seeds/samples. C/W/D must each gain at least 0.1 mean correctness over A
without a regression in any dataset's mean. This is a **descriptive fixture
gate**, not a statistical significance or real promotion decision. CW remains
the composition of C and W and has no independent search session.

Default C and W clear the gate. D regresses on the toy GSM8K route and is vetoed.
The veto ends the experiment without reselection or final-audit grading. Its
scores never enter an editor request. A separate all-pass fixture demonstrates
the audit path:

```bash
uv run python ../examples/weight_evolution/qwen_adaptive/build_fixture.py \
  --output ./qwen-adaptive-pass-fixtures --confirmation-outcome pass
uv run python ../examples/weight_evolution/qwen_adaptive/run.py \
  --fixtures ./qwen-adaptive-pass-fixtures run-fixtures \
  --output ./qwen-adaptive-pass
```

This creates a distinct hand-authored synthetic experiment. Passing evidence
cannot replace a veto in the same output directory. When all arms confirm, the
final audit reuses M5's prerecorded observations only if every evaluation-cell
digest matches exactly. Different selected contents require different evidence.
All reports retain `model_execution_performed=false`, `promotion_eligible=false`
or the equivalent comparison claim flags, and explicit fixture labels.

## Files and remaining gaps

The pinned bundle contains `data.json`, `workflow.json`, `confirmation.json`,
and byte/hash locks. Output contains the workflow manifest, `search/{C,W,D}/`
journals and selection/seal receipts, the all-arm `selection.json`, a separate
`confirmation/` claim/report/completion, and the final `complete.json`. `audit/`
and `audit-plan.json` exist only after all arms confirm. Exact resume regrades
and verifies retained evidence without additional provider/probe calls.

Real candidate generation, tensor-editing programs, inference-backed probes,
process isolation/hard deadlines, benchmark practice/confirmation selection and
contamination review, independent repeated-search trials, GPU acceptance, and
published-score reproduction remain open. Trusted in-process fixture boundaries
are not an adversarial sandbox or remote attestation. TypeScript execution
parity remains deferred because this experimental workflow is in Python.

See [M7 validation](MILESTONE7_VALIDATION.md) and the still-open
[GPU acceptance/reproduction gaps](../qwen_reproduction/MILESTONE4_VALIDATION.md).
