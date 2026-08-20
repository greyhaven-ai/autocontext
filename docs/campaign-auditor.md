# Read-only campaign auditor

The optional Python `CampaignAuditor` performs bounded, independent reviews at four checkpoints: `pre_promotion`, `inconclusive_gate`, `integrity_alert`, and `final_completion`.

The auditor is advisory. It cannot mutate context bundles, scores, deterministic monitor state, or promotion decisions. A configured policy may translate high-severity findings into `review_required` or a critical finding into `safe_pause_recommended`; an operator or separate control policy must act on that recommendation.

## Evidence boundary

`build_campaign_audit_packet` creates a typed, reconstructible packet containing:

- campaign identity and protocol lane metadata;
- bundle and evaluator lineage;
- raw metric summaries and reconstruction references;
- gate decisions and their declared evidence level;
- compact negative-result summaries, integrity alerts, and artifact references.

The builder uses an allowlist, omits `holdout_refs`, strips credential-shaped
keys, replaces free-text claims, reasons, alerts, and artifact summaries with
bounded categorical descriptions, and applies the sharing redactor to every
remaining string. Callers must explicitly declare whether their input included
hidden holdout answers; review refuses the packet when
`hidden_holdout_answers_included` is true. Accepted packets declare
`access_scope: read_only` and `credentials_included: false`.

## Routing and bounds

`CampaignAuditConfig` requires an auditor provider/model and the proposer provider/model. Reusing the same route is rejected unless `allow_same_route` is explicitly set. The role is recorded as frozen and non-trainable.

Calls are bounded by checkpoint allowlist, per-campaign call count, input
characters, and output tokens; estimated cost is recorded for accounting.
Evidence plus the complete
auditor configuration form the cache key, so only an unchanged packet reviewed
under the same policy and route reuses a completed audit. A cross-process
campaign lock makes the cache lookup, budget claim, call, and record write one
transaction; concurrent identical callers cannot duplicate a completed call.
Legacy records that predate the configuration fingerprint are charged as prior
calls but are not reused: their original policy and route cannot be proven.
Operators upgrading an existing audit store should account for that
conservative migration when setting the campaign call limit.
The durable budget counts provider dispatches and unresolved dispatch
reservations. Pre-call validation failures, known local submission failures,
and budget-exhaustion decisions do not consume a slot. Exhaustion responses are
not persisted, so unique denied packets cannot grow the store or prevent an
operator from deliberately raising the budget. Each provider dispatch first
writes an append-only attempt claim. A malformed response, audit-record write
failure, or process death therefore fails closed against the call budget
instead of reopening an untracked paid-call slot.
If a claim artifact is later lost, its bound audit record remains a fallback
budget claim rather than silently reopening the slot.
Invalid responses, provider failures, and response-wait timeouts create
separate attempt artifacts while deterministic monitoring continues unchanged.

The current thread-level timeout bounds how long the control plane waits; it
does not cancel a provider request that has already started. Production clients
must enforce their own transport deadline/cancellation. Until that lifecycle is
wired, operators should not automatically retry a timed-out paid request.

## Findings and disposition

Findings cite packet artifact URIs and cover non-comparable cohorts, evaluator drift, leakage, missing reconstruction evidence, infrastructure failures misclassified as candidate failures, unsupported causal claims, and repeated unchanged experiments. Deterministic integrity preflight is merged with the independent LLM review, but remains advisory through this API.

`CampaignAuditStore` persists each review by campaign, evidence fingerprint,
and configuration fingerprint. `make_operator_disposition` and
`add_disposition` append an operator response (`accepted`, `dismissed`,
`mitigated`, or `deferred`) without rewriting the audit; disposition updates
use the same cross-process lock so concurrent operators do not lose a response.
The legacy two-argument `read_by_fingerprint(campaign_id, evidence_fingerprint)`
remains deterministic when retry or configuration history exists: it returns
the latest completed review, or the latest valid attempt when none completed.

The campaign auditor is implemented first in Python because long-running campaign execution is currently a Python control-plane surface; TypeScript parity is deferred.
