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

The builder uses an allowlist, omits `holdout_refs` and all held-out answers, strips credential-shaped keys, and applies the sharing redactor to every string. The packet declares `access_scope: read_only`, `hidden_holdout_answers_included: false`, and `credentials_included: false`.

## Routing and bounds

`CampaignAuditConfig` requires an auditor provider/model and the proposer provider/model. Reusing the same route is rejected unless `allow_same_route` is explicitly set. The role is recorded as frozen and non-trainable.

Calls are bounded by checkpoint allowlist, per-campaign call count, input characters, output tokens, timeout, and estimated cost. The packet fingerprint is the cache key, so an unchanged duplicate checkpoint returns the existing audit without another model call. Invalid responses, provider failures, and timeouts create a failure artifact while deterministic monitoring continues unchanged.

## Findings and disposition

Findings cite packet artifact URIs and cover non-comparable cohorts, evaluator drift, leakage, missing reconstruction evidence, infrastructure failures misclassified as candidate failures, unsupported causal claims, and repeated unchanged experiments. Deterministic integrity preflight is merged with the independent LLM review, but remains advisory through this API.

`CampaignAuditStore` persists each review by campaign and evidence fingerprint. `make_operator_disposition` and `add_disposition` append an operator response (`accepted`, `dismissed`, `mitigated`, or `deferred`) without rewriting the audit.

The campaign auditor is implemented first in Python because long-running campaign execution is currently a Python control-plane surface; TypeScript parity is deferred.
