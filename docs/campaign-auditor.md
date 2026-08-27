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
remaining string. Artifact references additionally drop URL user information,
query strings, and fragments, so signed URLs and bearer query parameters never
cross the model boundary. When redaction changes a local artifact path or URL
path, its replacement includes a stable digest of the original reference so
distinct artifacts cannot collapse onto one redaction token. Production
campaign runs reference their immutable persisted plan and supply its content
digest. Sensitive campaign/run/scenario identifiers use
stable, label-scoped digests rather than one shared redaction token, preserving
cache and store separation. Callers must explicitly declare whether their input included
hidden holdout answers; review refuses the packet when
`hidden_holdout_answers_included` is true. Accepted packets declare
`access_scope: read_only` and `credentials_included: false`.
Every accepted packet also carries `boundary_provenance:
whitelisted_redacted_v1`, a digest over the complete metadata payload, and a
process-local HMAC signature. Review recomputes the digest, verifies the
signature, and scans every surviving string for credential and holdout-answer
markers before prompt rendering, so packet data alone cannot forge the seal.
The signature is excluded from the evidence fingerprint, preserving stable
cross-process cache identity.

`CampaignCheckpointPacketFactory(CampaignAuditPacketIdentity(...))` is the
production adapter. The callable factory accepts scheduler/lifecycle
`(checkpoint, evidence)` inputs; its `context_pre_promotion(candidate,
comparison, trials)` method matches the context-bundle promotion adapter. Both
paths extract only typed metadata and return sealed packets.

## Routing and bounds

`CampaignAuditConfig` requires an auditor provider/model and the complete set of
routes that can propose bundles. Each route persists a credential-free,
canonical backend identity plus its model. Independence is checked against
that resolved endpoint/client identity and model, so provider aliases such as
`openai` and `openai-compatible` do not count as independent when they reach
the same endpoint with the same model. Reusing any proposer route is rejected
unless `allow_same_route` is explicitly set. The legacy singular
`proposer_provider`/`proposer_model` fields remain the fallback when no route
set is supplied. Each audit persists the normalized route set, and that set is
part of the configuration fingerprint. The role is recorded as frozen and
non-trainable. Production composition passes live routes through
`build_live_campaign_auditor(..., proposer_routes=[CampaignAuditRoute.resolved(...), ...])`.
The compatibility `(provider, model)` form resolves the provider's default
endpoint and remains available for callers without a custom route.

Calls are bounded by checkpoint allowlist, per-campaign call count, input
characters, and output tokens; estimated cost is recorded for accounting.
When a section exceeds its bound, representative integrity strata are retained,
the full set is still checked for evaluator/cohort mismatch, and the packet
records `evidence_truncated`. Truncation is a high-severity deterministic
finding, so `review_required_on_high` cannot quietly authorize promotion from
an incomplete packet.
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
Pre-dispatch cancellations are persisted for reporting without consuming a
call slot. Once native submission begins, a raised exception is charged
conservatively because provider acceptance is ambiguous. Malformed responses
retain known token usage, latency, and estimated cost in the failed record.
If a claim artifact is later lost, its bound audit record remains a fallback
budget claim rather than silently reopening the slot.
Invalid responses, provider failures, and response-wait timeouts create
separate attempt artifacts while deterministic monitoring continues unchanged.

Enabled production auditors require a client `start_generate()` boundary that
returns an `AuditorCallHandle`. On deadline or campaign cancellation, the
auditor calls `cancel()` and records `timed_out`/`canceled` only when the handle
confirms termination; refusal is a failed audit. This avoids leaked non-daemon
wait threads. Synchronous legacy clients are accepted only with the explicit
`allow_uncancellable_transport` escape hatch, intended for trusted migration
and tests rather than production.
`build_cancellable_auditor_client(client, process_start_method=None)` wraps
an existing synchronous client in a per-call POSIX process-group boundary. A
readiness handshake proves `setsid()` isolation before provider dispatch. Its
handle terminates the complete group, escalates to `SIGKILL`, and verifies the
leader and descendants are gone on cancellation and after a response,
providing a hard local deadline even for a blocking SDK call; the durable paid-call claim remains
conservative because closing a connection cannot prove remote rejection. The
adapter fails closed before dispatch on platforms without the required process
group contract. Pipe and
process construction failures are classified as pre-submission and release the
reserved call slot. The default uses `fork`; callers may select another
available POSIX start method explicitly when their client has stricter process
construction requirements.

`CampaignAuditCheckpointRunner` invokes one typed packet factory for
`pre_promotion`, `inconclusive_gate`, `integrity_alert`, and
`final_completion`. The live context-bundle coordinator invokes the first two
and reports evaluator exceptions through `integrity_alert`; a high/critical
pre-promotion policy outcome holds the active incumbent without changing
deterministic scores. The live campaign scheduler emits integrity alerts for
infrastructure failures and a final completion packet when its service or
bounded run ends. Evidence and configuration fingerprints make duplicate or
restart-replayed checkpoints idempotent.

Set `AUTOCONTEXT_CAMPAIGN_AUDITOR_ENABLED=true` to compose this runner. Normal
generation must also set `AUTOCONTEXT_CONTEXT_BUNDLE_PROMOTION_ENABLED=true`,
because context promotion supplies its live audit checkpoints; generation
fails fast instead of silently enabling an auditor that will never run.
`autoctx campaign run` supplies scheduler checkpoints directly and does not
depend on context promotion. The provider/model, proposer route, bounds,
timeout, policy, and cost rates are configured through the corresponding
`AUTOCONTEXT_CAMPAIGN_AUDITOR_*` settings. A dedicated
`AUTOCONTEXT_CAMPAIGN_AUDITOR_BASE_URL` and
`AUTOCONTEXT_CAMPAIGN_AUDITOR_API_KEY` may be supplied for
supported providers. This role never inherits agent/judge endpoints or generic
agent/judge keys; without dedicated values it uses only the selected
provider's default endpoint and native environment credential. Dedicated
endpoints with credentials, query strings, or fragments and endpoint/key
settings unsupported by the selected provider fail closed. Construction also
fails if the enabled auditor route is unavailable or is identical to a
proposer route without the explicit override.

## Findings and disposition

Findings cite packet artifact URIs and cover non-comparable cohorts, evaluator drift, leakage, missing reconstruction evidence, infrastructure failures misclassified as candidate failures, unsupported causal claims, and repeated unchanged experiments. Deterministic integrity preflight is merged with the independent LLM review, but remains advisory through this API.

`CampaignAuditStore` persists each review by campaign, evidence fingerprint,
and configuration fingerprint. `make_operator_disposition` and
`add_disposition` append an operator response (`accepted`, `dismissed`,
`mitigated`, or `deferred`) without rewriting the audit; disposition updates
use the same cross-process lock so concurrent operators do not lose a response.
The checkpoint runner applies the latest disposition on cache replay:
`dismissed` and `mitigated` resolve the hold to advisory, `deferred` keeps a
safe pause, and `accepted` preserves the auditor's original policy outcome.
Operators can inspect and resolve these records without editing artifacts:

```bash
autoctx campaign audit list CAMPAIGN_ID --json
autoctx campaign audit resolve CAMPAIGN_ID EVIDENCE_FINGERPRINT \
  --operator operator@example.com --disposition dismissed \
  --rationale "Verified false positive"
```

Scheduler reports include the durable audit records and their operator
dispositions by campaign.
Sensitive campaign and run identifiers are redacted to stable label-scoped
digests inside sealed packets and audit records. Public store and CLI lookups
apply the same normalization, so operators may continue to use the original
campaign ID without knowing its sealed representation. Filesystem campaign,
record/run, and attempt segments always use labeled deterministic
SHA-256-derived names, including for short identifiers. This prevents
case-insensitive aliases and path traversal. Reads remain compatible with the
legacy literal/percent-digest layout, and the next campaign-locked write moves
valid legacy records and call claims to the canonical digest layout. When a
canonical copy already exists, migration merges identical audits' disposition
history in deterministic timestamp/identity order; conflicting audit or claim
bindings fail closed and leave the legacy artifact intact.
The legacy two-argument `read_by_fingerprint(campaign_id, evidence_fingerprint)`
remains deterministic when retry or configuration history exists: it returns
the latest completed review, or the latest valid attempt when none completed.

The campaign auditor is implemented first in Python because long-running campaign execution is currently a Python control-plane surface; TypeScript parity is deferred.
