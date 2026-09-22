# Applicability-aware skill and model routing (Python)

AC-1020 adds an opt-in router around the [executable-skill bridge](executable-skills.md).
It tries an explicitly enrolled skill, an eligible registered model, then a
configured general model. Every accepted result must pass the same objective
profile-v1-to-v2 verifier. The result is a proposal; the router never applies a
migration or grants the model tools.

This first implementation uses deterministic applicability checks. It does not
train an AgentRun classifier, interpret model confidence as permission, or claim
calibrated semantic routing. Complete route quality/cost promotion remains
AC-1021; the held-out comparison remains AC-1029. The API changes no global
provider settings and is not wired into automatic production routing.

## Invoke the router

After explicitly enrolling a candidate as shown in the bridge guide:

```python
from pathlib import Path

from autocontext.execution.skill_routing import route_schema_migration
from autocontext.execution.skill_routing_models import RoutingBudget, SkillRoutingConfig
from autocontext.training.model_registry import ModelRegistry

result = route_schema_migration(
    store,
    ModelRegistry(Path("models")),
    '{"schema_version":1,"name":"Ada","enabled":true}',
    config=SkillRoutingConfig(
        enabled=True,
        bundle_digest=bundle.digest,
        budget=RoutingBudget(wall_seconds=30.0, max_attempts=3),
    ),
    trace_root=Path("traces"),
    mode="evaluation",
)
if result.status == "success":
    print(result.output_json)  # Independently verified, canonical JSON.
else:
    print(result.reason, [(a.route, a.reason) for a in result.attempts])
```

`enabled` and `allow_network` both default to false. The example enables only
the skill tier. Omitted model tiers are recorded as skipped. With models
configured, a missing, stale, inapplicable or invalid skill may fall back.
Unverified cleanup or cancellation stops the request rather than starting
another execution. Each tier is considered at most once. Model/endpoint
combinations are marked as attempted only when dispatch starts; a skipped
specialized tier can still use the configured general fallback at that endpoint.

`mode` is required. `evaluation` explicitly pins an inactive candidate for
replay. `serving` preserves the bridge's active-bundle and durable-promotion
checks, including revocation checks before acceptance. This is a skill gate,
not evidence that the complete fallback configuration is production-approved.
Keep the route configuration with matched evaluation records for AC-1021.

## Configure model fallback

`SkillRoutingConfig.learned_playbook` optionally supplies up to 8,192 characters
of frozen textual context to model calls. It defaults to empty, is bound into
the config digest and durable trace, and counts toward input-token admission.
It does not change the task contract, objective verifier, tools or skill source.
The [four-arm reuse study](../benchmarks/skill_reuse/README.md) uses this field
for matched textual controls and the executable route's model fallback.

Models must support one HTTP completion, bounded output tokens, usage receipts
and a response identifying the actual served model. Supported transports are
`openai-compatible` and `anthropic`. Agent CLI adapters and direct training
runtimes are not dispatched. An operator can serve a trained checkpoint through
a compatible HTTP endpoint and bind it to its existing registry record.

```python
from autocontext.execution.executable_skills import CONTRACT, SCENARIO
from autocontext.execution.skill_routing import routing_evaluator_identity
from autocontext.execution.skill_routing_models import ModelTarget

# Replace these fixture identifiers and prices with validated operator settings.
specialized = ModelTarget(
    provider="openai-compatible",
    model="profile-migrator-v1",
    base_url="http://127.0.0.1:8000/v1",
    api_key_env="MIGRATION_API_KEY",
    max_input_tokens=8192,
    max_output_tokens=1024,
    input_cost_per_1k=0.0,  # Only appropriate for a model with no token charge.
    output_cost_per_1k=0.0,
    timeout_seconds=10.0,
)

# Use the existing, explicitly activated DistilledModelRecord. Its scenario
# must be SCENARIO and its runtime_types must include "provider".
record = registry.load("your-existing-model-artifact")
assert record is not None and record.scenario == SCENARIO
record.metadata["skill_routing"] = {
    "contract": CONTRACT,
    "target_digest": specialized.digest,
    "evaluator_identity": routing_evaluator_identity(),
}
registry.register(record)

config = SkillRoutingConfig(
    enabled=True,
    allow_network=True,
    bundle_digest=bundle.digest,
    specialized=specialized,
    specialized_backend=record.backend,
    # general=another_explicit_model_target,
)
```

The router reuses `resolve_provider_for_context` and `ModelRegistry`. It requires
an active record with compatible scenario/backend/runtime and exact contract,
endpoint/configuration and evaluator bindings. `specialized_backend` selects
the training registry backend (for example `mlx`); when omitted it defaults to
the target's transport. The explicit metadata binds the registered checkpoint
to the served model identifier. The record is checked again after completion.
Changing the code, verifier, SDK versions, endpoint or target configuration
requires revalidation and updated bindings. Metadata is an operator assertion,
not cryptographic attestation of remote model weights.

`general` is an explicit general-purpose target and needs no specialized-model
registry entry. `override` bypasses the skill and all other models, preserving
the caller's selected target even if that call fails. Network permission,
offline restrictions, budgets and verification still apply. `AUTOCONTEXT_OFFLINE=1`
uses the existing endpoint policy: remote endpoints are blocked; literal
loopback endpoints are allowed. No ambient proxy or alternate SDK endpoint is
inherited, and HTTP redirects are disabled. Credentials are read from the specified environment variable and
are never written to the route trace.

An output is accepted only when both the requested and reported served model
match the configured identifier. Choose a pinned identifier that the endpoint
actually reports; an alias resolving to a different identifier fails closed.
Models may return exactly `{"abstain":true}`. Unknown schema versions, malformed
inputs and extra fields can reach the general tier, but cannot produce an
accepted migration outside the pilot's known contract. A confident response
does not expand the verifier's authority.

## One budget across the request

The request shares one wall deadline, up to three attempted tiers, a token
budget and a model-spend budget. Each model dispatch reserves its full declared
input/output token ceilings and corresponding prices before it starts. Input
admission uses UTF-8 byte length plus the fixed prompt and a 2,048-token envelope
allowance. Operators must validate that this bounds their backend's tokenization
and hidden request overhead. Prices are required operator declarations; there
is no unknown-model fallback price. Spend reservations round upward to a
microdollar so tiny positive charges cannot pass a zero-spend budget.

Successful receipts reconcile the reservation using original HTTP JSON usage,
captured before SDK parsing can coerce booleans, strings or floats into integers
(including directional/total consistency), and the
larger of declared-price cost and any provider-reported cost. Missing, malformed,
contradictory or excessive counters/costs stop escalation. Failed calls without
a valid receipt retain their complete reservation. Budget exhaustion never
resets the request or silently chooses a cheaper unconfigured provider.
Known OpenAI detail counters must fit their directional totals. Additional
billing counters, including nonzero Anthropic cache creation/read counters,
require a future accounting extension and currently fail closed.

The absolute request deadline is carried through skill preflight, Docker
execution, model-worker setup and SDK initialization. Expired preflight cannot
start another execution. Runtime timeouts use the remaining request time without
rewriting the candidate's immutable limits or restarting the clock.
Callers running multiple requests can also pass an outer `request_budget`
(`RuntimeBudget`). Its absolute deadline caps the configured route budget from
router initialization through skill/model dispatch and final verification.

Each model call runs in a bounded, cancellable worker using repository-owned
provider code, with SDK retries disabled. Pass a `threading.Event` as `cancel`
to stop an in-flight request. Worker output and process-tree cleanup are bounded;
cleanup has a small additional grace allowance. Cancellation/deadline checks
also run after verification and the final trace write. Killing a local client
cannot retract a request already received by a remote provider, so unknown
outcomes keep their full reservation. This is not a remote billing guarantee.
Executable candidate code continues to run only in the bridge's Docker boundary.

`model_calls` counts worker dispatch attempts, each permitting at most one
completion request. A worker may fail before making that request. Accounted
model cost excludes Docker/CPU/memory/storage cost and is not a total-lifecycle
cost or savings claim. `model_cost_complete=false` indicates a retained
reservation or a declared-price estimate without a provider cost receipt.

## Retained decisions and validation

Each request writes `trace_root/execution-routing/<request_id>.json`, initially,
before each dispatch and on completion. Trace-write failure prevents further
dispatch or acceptance. Records include bounded raw input and its digest,
configuration and evaluator identity, attempted/skipped routes, reasons,
artifact/model/endpoint identities, latency, token/cost reservations and
accounting provenance. Only verified success includes `output_json`. Treat the
trace directory as task data: raw input can contain sensitive information.

The existing [fixture](../../examples/executable_skills/run.py) now exercises
both the bridge and router with real Docker and no model calls. CI also selects
`test_real_docker_routing_skill_then_model`: a wrong Docker result is rejected,
then a local HTTP model fixture supplies a verified fallback. Other regressions
cover stale registrations, unsupported inputs, overrides, budget exhaustion,
provider failure, one-dispatch behavior, timeout and cancellation. These are
engineering fixtures, not live-model quality or held-out promotion evidence.

TypeScript parity is deferred because this slice uses the Python bundle,
registry and provider boundaries. No TypeScript routing support is claimed.
