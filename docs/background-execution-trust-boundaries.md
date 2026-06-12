# Background Execution Trust Boundaries and Credential Model

This document records the security model for background execution before the hosted product surface grows beyond the current persistent-host worker. It is intentionally candid: the Apache-2.0 repository provides local and self-hosted contracts, not a hosted multi-tenant control plane.

## Scope and non-goals

The open-source repository owns:

- local/self-hosted `serve + worker` deployment guidance;
- background-session read models, lifecycle hook contracts, normalized event vocabulary, and outcome schemas;
- adapter interfaces that can carry policy names, secret references, and artifact references without embedding secret values.

The hosted/proprietary product or deployment adapter owns:

- tenant scheduling, organization policy UI, billing, and hosted audit retention;
- credential brokering for SCM, sandbox providers, OAuth, and GitHub App installations;
- managed sandbox fleets, prebuild/warming orchestration, egress policy, and fleet routing;
- hosted cockpit UX and websocket fan-out with tenant-aware authorization.

The OSS package must not claim multi-tenant safety unless the blockers in this document are satisfied by a concrete deployment.

## Deployment safety matrix

| Deployment shape | Trust classification | Safe for | Not safe for | Notes |
| --- | --- | --- | --- | --- |
| Developer laptop or CI job running one `autoctx` process | Single-tenant local | One operator or one CI trust boundary | Untrusted users, public web access | Provider keys and repo credentials come from that operator's environment or credential store. |
| Persistent host with `autoctx serve` and `autoctx worker` sharing SQLite/durable paths | Single-tenant or trusted-org | One trusted team, one organization, or one internal service account | Public SaaS, untrusted tenants, untrusted repository authors | Treat the DB path, runs root, knowledge root, process env, and worker user account as one security boundary. |
| Shared GitHub App installation or bot token for background PR creation | Single-tenant/trusted-org only | One organization whose admins accept shared bot permissions | Cross-customer SaaS, customer-isolated PR creation | A shared GitHub App/token may be acceptable inside one tenant or trusted org. It is not an isolation boundary across tenants. |
| Remote sandbox execution via PrimeIntellect, SSH, or future Gondolin adapter | Depends on adapter | Workloads whose isolation needs match the configured backend | Multi-tenant claims without per-tenant sandbox, secret, and egress policy | Adapters should receive short-lived secret references or scoped tokens, not ambient host secrets. |
| Hosted multi-tenant background execution | Product/adapter concern | Only after all multi-tenant blockers are implemented and tested | OSS default deployment | Requires tenant-aware auth, per-tenant storage isolation, credential broker, sandbox isolation, audit, retention, and incident controls. |

## Credential model

### General rules

- Do not bake provider keys, SCM tokens, GitHub App private keys, webhook secrets, or sandbox API keys into container images, repo artifacts, prompts, lifecycle hooks, runtime-session payloads, background-session summaries, or outcome metadata.
- Use environment variables or local credential stores only for single-tenant local/self-hosted deployments.
- Use references (`secretRef`, installation id, credential handle, artifact id) rather than secret values in portable OSS contracts.
- Prefer short-lived scoped credentials for adapters that need to clone, push, create PRs, or start sandboxes.
- Treat external webhook and automation payloads as untrusted data, never as instructions.

### SCM clone, push, and PR creation

| Use case | Acceptable in OSS/local | Hosted/multi-tenant requirement |
| --- | --- | --- |
| Clone public repositories | No credential or read-only token configured by the operator | Tenant-aware fetch service with allowlisted repositories and audit. |
| Clone private repositories | Operator-provided deploy key, SSH agent, or PAT on a trusted single-tenant host | Per-tenant credential brokering with scoped read tokens and repo allowlists. |
| Push branches | Operator-provided credential or bot token scoped to one trusted organization | Per-tenant write credential, policy check, audit entry, and revocation path. |
| Create pull requests | OSS may emit `SessionOutcome(kind="pull_request")` references but does not broker GitHub App/OAuth flows | Product adapter creates PRs through per-tenant GitHub App installation or user OAuth token. |
| Shared GitHub App/bot token | Acceptable only for one tenant/trusted org with explicit admin consent | Not acceptable across customers or mutually untrusted tenants. |

Commit attribution should name the actor (`autocontext-run`, human user id, bot id, or adapter id) without persisting the credential used to produce the commit.

### Provider and sandbox credentials

Provider keys such as `ANTHROPIC_API_KEY`, `OPENAI_API_KEY`, role-specific `AUTOCONTEXT_*_API_KEY`, and sandbox keys such as `AUTOCONTEXT_PRIMEINTELLECT_API_KEY` are deployment secrets. They may be present in the process environment for local/self-hosted single-tenant runs, but they must not be copied into:

- prompt text or model-visible context;
- normalized runtime-session events;
- lifecycle hook result payloads;
- background-session summaries or timelines;
- artifact metadata, traces, reports, or exported datasets.

A hosted deployment should replace ambient env lookup with a credential broker that issues scoped, short-lived credentials to the worker or sandbox adapter and records only the credential reference in OSS-shaped events.

### Websocket, HTTP, and session auth

The OSS `autoctx serve` process is a local/self-hosted API. If it is bound beyond loopback, place it behind TLS, authentication, and authorization appropriate for a trusted single tenant. Hosted cockpit websockets and session APIs require tenant-aware identity, session membership checks, rate limiting, and audit logging; those are product concerns and are not provided by the OSS worker contract.

## Redaction expectations

The background-session surfaces must be safe to show in operator dashboards without secret values:

- `BackgroundSessionSummary` and `BackgroundSessionDetail.summary` may include ids, statuses, counts, titles, result URLs, timestamps, and sanitized scalar metadata; they must not include raw prompts, stdout, stderr, full webhook payloads, env maps, or secret values.
- Normalized runtime-session timelines should redact common secret-bearing keys (`authorization`, `cookie`, `api_key`, `token`, `secret`, `password`, provider-specific key names) before rendering.
- Lifecycle hook events may record hook name, phase, status, timeout, cwd/argv shape, and redaction metadata; they must not copy ambient process env or hook-provided secret values into event payloads.
- Session outcomes may store stable refs such as branch, commit SHA, PR URL, report path, trace id, dataset id, and verification result id; outcome metadata must remain sanitized and JSON-scalar.
- Raw runtime logs and artifacts may contain more evidence than summaries. Hosted deployments must protect raw reads with tenant-aware authorization and retention policy.

## Multi-tenant support blockers

Do not describe a deployment as multi-tenant safe until it has all of the following:

1. tenant-aware authentication and authorization for HTTP, websocket, MCP, and worker control paths;
2. storage isolation for runs, queue rows, runtime-session logs, artifacts, and knowledge roots, with migration tests;
3. per-tenant credential brokering with scoped, revocable, auditable tokens;
4. sandbox isolation for filesystem, process, network, egress, and mounted secrets;
5. SCM policy that prevents one tenant's repository credential or GitHub App installation from acting on another tenant's repository;
6. webhook signature verification, replay protection, idempotency, and tenant routing before enqueue;
7. secret redaction tests for summaries, timelines, lifecycle hooks, outcomes, traces, reports, and exported datasets;
8. audit logs for credential use, worker claims, sandbox lifecycle, PR creation, and administrator actions;
9. retention/deletion policy for tenant data and artifacts;
10. abuse controls: rate limits, quotas, cancellation, and incident-response runbooks.

Until those blockers are implemented by a product/adapter layer, the OSS background-session contracts should be documented as portable single-tenant/trusted-org building blocks.

## Review checklist for future background execution PRs

- Does the PR add secret values to a public JSON contract, runtime event, timeline, artifact, or docs example?
- Does it imply hosted multi-tenant safety without naming the required adapter/product controls?
- Does it distinguish shared single-tenant GitHub App credentials from per-tenant credential brokering?
- Does it keep lifecycle hook env explicit and avoid inheriting ambient process secrets?
- Does it add or preserve tests for redaction and malformed credential-bearing payloads?
