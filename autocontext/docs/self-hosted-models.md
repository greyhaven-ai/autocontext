# Running autocontext on your own hardware

Every role in the loop can run against an endpoint you host. Nothing about the
shipped defaults changes: without the settings below, autocontext behaves
exactly as it always has.

Every routing table and error message on this page was produced by executing
the code, not read off it. Where something is untested, it says so.

For the full provider matrix and every environment variable,
see [agent-integration.md](agent-integration.md). This page is the narrative
version: what to set, what you get, and why.

## The shortest version

```bash
ollama serve                      # already listening on :11434
ollama pull llama3.1:8b

AUTOCONTEXT_AGENT_PROVIDER=ollama \
AUTOCONTEXT_LOCAL_MODEL=llama3.1:8b \
  autoctx run my_task
```

That is the whole configuration. No API key, no per-role model variables, no
base URL — `ollama` defaults to `http://localhost:11434/v1`.

## What each role actually gets

Naming the provider and nothing else:

```
AUTOCONTEXT_AGENT_PROVIDER=ollama

role         provider   model      $/1k
competitor   ollama     llama3.1   0.0
analyst      ollama     llama3.1   0.0
coach        ollama     llama3.1   0.0
architect    ollama     llama3.1   0.0
curator      ollama     llama3.1   0.0
translator   ollama     llama3.1   0.0
```

`llama3.1` is the per-provider default. Before this work every one of those
rows said an Anthropic model id (`claude-opus-4-6` or `claude-sonnet-4-5` at
the time), which a local server cannot
serve — the request failed at the endpoint rather than at configuration time.

`AUTOCONTEXT_LOCAL_MODEL` replaces every slot you have not configured
explicitly, which is usually what you want when one endpoint serves one model:

```
AUTOCONTEXT_AGENT_PROVIDER=ollama
AUTOCONTEXT_LOCAL_MODEL=llama3.1:8b     → every role: llama3.1:8b, $0.00
```

An explicit `AUTOCONTEXT_MODEL_<ROLE>` always wins over it.

### Hosted providers use real tier defaults in automatic mode

With `AUTOCONTEXT_ROLE_ROUTING=auto`, providers backed by multi-model catalogs
do not reuse one model for every role:

| class    | OpenAI / OpenAI-compatible | OpenRouter                   |
| -------- | -------------------------- | ---------------------------- |
| frontier | `gpt-5.6-sol`              | `anthropic/claude-opus-5`    |
| mid-tier | `gpt-5.6-terra`            | `anthropic/claude-sonnet-5`  |
| fast     | `gpt-5.6-luna`             | `anthropic/claude-haiku-4.5` |

`openai-compatible` names a wire protocol, not a guaranteed model catalog. If
that transport points at llama.cpp, LM Studio, Hermes, or another endpoint
serving one model, set `AUTOCONTEXT_LOCAL_MODEL` to that endpoint's exact model
id. It overrides all otherwise-unset tier slots and prevents requests for the
hosted OpenAI ids above.

### Cost is zero because hosting is local, not because the model is small

Self-hosted inference has no per-token API cost however capable the model is.
That is a property of where it runs, not of what it can do, and the two are now
separate:

```
AUTOCONTEXT_AGENT_PROVIDER=openai-compatible                     → $0.015 / $0.003 / $0.001 per role tier
AUTOCONTEXT_AGENT_PROVIDER=openai-compatible
AUTOCONTEXT_PROVIDER_HOSTING=local                               → $0.00 for every role
```

`ollama`, `vllm` and `mlx` are known-local and need no declaration.
`openai-compatible` is the generic escape hatch, so it is assumed remote —
declare `local` when you are pointing it at your own box (llama.cpp, LM Studio,
a private vLLM behind a gateway). Over-reporting cost on a self-hosted endpoint
is a nuisance; under-reporting it on a paid API is a surprise on the invoice,
which is why the ambiguous case defaults the safe way.

## Declaring what your endpoint can do

Capability used to be inferred from the transport name: `ollama` and `vllm`
were hardcoded mid-tier, so a self-hosted frontier-class model was permanently
misclassified and could never serve `architect`. Now the endpoint declares it.

```bash
AUTOCONTEXT_PROVIDER_CAPABILITY=frontier   # or mid_tier, or fast
```

Declarations apply only to locally hosted transports. A cloud endpoint has a
knowable capability, and confining the override is what keeps Anthropic
behavior identical whether or not you set this.

A role asks for a capability; an endpoint has one. Asking a `fast` endpoint for
frontier work does not make it frontier — the request is **clamped down** to
what the endpoint offers, never raised:

```
AUTOCONTEXT_AGENT_PROVIDER=vllm
AUTOCONTEXT_ROLE_ROUTING=auto
AUTOCONTEXT_PROVIDER_CAPABILITY=fast

architect    class=fast     (asked for frontier, got what the endpoint declared)
```

### Per-role declarations

Each role that takes a provider override takes a capability and hosting
override too, for the case where roles sit on different boxes:

```bash
AUTOCONTEXT_AGENT_PROVIDER=vllm
AUTOCONTEXT_ROLE_ROUTING=auto
AUTOCONTEXT_PROVIDER_CAPABILITY=frontier
AUTOCONTEXT_ARCHITECT_PROVIDER=vllm
AUTOCONTEXT_ARCHITECT_PROVIDER_CAPABILITY=frontier
```

```
role         provider   class
competitor   vllm       frontier
analyst      vllm       mid_tier
architect    vllm       frontier     ← declared
curator      vllm       fast
```

The same pattern exists for `COMPETITOR`, `ANALYST` and `COACH`, and for
`_PROVIDER_HOSTING`.

### Typos fail before the run starts

```
AUTOCONTEXT_PROVIDER_CAPABILITY=excellent

ValidationError: provider_capability
  Input should be '', 'fast', 'mid_tier' or 'frontier'
```

Not partway through a generation when one role happens to take the path that
reads it.

## Structured output

This is the setting that matters most on open weights, and the one whose
absence is easiest to miss, because the failure is silent.

Role outputs used to be recovered by scraping markdown headings. Measured on
`llama3.1:8b` over 20 trials, using the analyst instruction autocontext
actually ships:

|                      | Findings   | Root Causes | Actionable Recs |
| -------------------- | ---------- | ----------- | --------------- |
| markdown scraping    | lost 20/20 | lost 20/20  | lost 20/20      |
| constrained decoding | lost 0/20  | lost 0/20   | lost 0/20       |

**That is not a reasoning failure.** The model's analysis was correct. It wrote
`### Findings` with `* ` bullets; the parser wanted `## Findings` with `- `
bullets. Two independent drifts, either one alone leaving the corresponding
typed array empty with nothing raised.

The raw response was not discarded. Python still passed it to the coach and
persisted it for readers such as the curator, while neither engine currently
uses those parsed analyst arrays to drive the loop. Constrained decoding makes
the typed contract reliable and normalizes the rendered markdown; it does not
decide whether the analyst's prose reaches the rest of the Python loop.

Nothing needs configuring: role calls carry their schema automatically.

| backend                              | how                                     | status                            |
| ------------------------------------ | --------------------------------------- | --------------------------------- |
| Ollama                               | `response_format: json_schema` on `/v1` | verified against Ollama 0.32.5    |
| vLLM, OpenAI-compatible              | same                                    | same request path; see note below |
| OpenAI                               | same                                    | same request path                 |
| Anthropic                            | not implemented                         | falls back, reports unconstrained |
| CLI runtimes (claude-cli, codex, pi) | cannot constrain                        | falls back, reports unconstrained |

A backend that rejects the parameter is retried once without it. It keeps
working, and the run records `constrained=false` — an unconstrained run is
visible rather than assumed. When output _is_ constrained, drift raises
`RoleOutputValidationError` instead of yielding an empty section.

The measurement and its harness are committed:
`docs/ac913-format-drift-measurement.json`, and
`autocontext/scripts/measure_format_drift_baseline.py`.

### Turning it off

```bash
AUTOCONTEXT_CONSTRAINED_OUTPUT=false
```

Role calls then carry no schema, backends report `constrained=false`, and
parsing falls back to markdown — the same path a backend without support
already takes, not a separate mode.

You are unlikely to want this on an open-weight model when you depend on the
typed contract or stable rendered markdown. It exists because constrained
decoding also changes OpenAI-compatible **cloud** models. The committed
measurement on this page covers format compliance on one 8B local model; it
does not establish a quality advantage. If a model's analysis gets worse under
constraint, this is the switch to use while you measure the tradeoff.

### The coach is the role that matters most

The analyst's sections are what the format-drift measurement above counts, but
the **coach** is where drift can change the loop's persistent state. The two
engines handle malformed coach output differently:

- TypeScript updates the playbook only when all six markers (three pairs) are
  present. Otherwise it keeps the previous playbook and reports exactly which
  markers were missing.
- Python discards a response with `PLAYBOOK_START` but no matching end marker.
  When there are no playbook markers at all, it instead stores the entire
  response as the playbook and emits a warning.

Measured on `llama3.1:8b`, 10 trials: 8 produced all six markers and 2 produced
none. Those two responses were dropped updates in TypeScript and free-form
playbook replacements in Python. Both outcomes are now visible to the operator.

Constrained decoding is the fix rather than the diagnosis, which is another
reason to leave it on.

## Local agentic execution

A local model can drive workspace-touching execution, not just generate text.
There are two ways in, and they are different things.

**Through a gateway** — treat Hermes as an OpenAI-compatible endpoint. This is
the form used in [agent-integration.md](agent-integration.md):

```bash
AUTOCONTEXT_AGENT_PROVIDER=openai-compatible
AUTOCONTEXT_AGENT_BASE_URL=http://localhost:8080/v1
AUTOCONTEXT_LOCAL_MODEL=hermes-3-llama-3.1-8b
AUTOCONTEXT_PROVIDER_HOSTING=local
```

`AUTOCONTEXT_LOCAL_MODEL` fills every unset role and tier slot. By contrast,
`AUTOCONTEXT_AGENT_DEFAULT_MODEL` configures only the underlying client; the
role resolver would still request the provider default (`gpt-5.6-terra`).

**As a CLI runtime** — `AUTOCONTEXT_AGENT_PROVIDER=hermes` drives the Hermes
binary as a subprocess, which is what gives it workspace access rather than
plain text generation:

```bash
AUTOCONTEXT_AGENT_PROVIDER=hermes
AUTOCONTEXT_HERMES_BASE_URL=http://localhost:8000/v1
AUTOCONTEXT_HERMES_API_KEY=no-key            # required by the client, unused locally
```

`runtimes/hermes_cli.py` exports those two as `OPENAI_BASE_URL` /
`OPENAI_API_KEY` to the subprocess, and a base URL takes precedence over any
provider setting.

Pi is a separate CLI/RPC runtime rather than an endpoint-configured equivalent.
Its active settings include `AUTOCONTEXT_PI_COMMAND`, `AUTOCONTEXT_PI_MODEL`,
`AUTOCONTEXT_PI_WORKSPACE` and `AUTOCONTEXT_PI_NO_CONTEXT_FILES`. The legacy
`AUTOCONTEXT_PI_RPC_ENDPOINT` and `AUTOCONTEXT_PI_RPC_API_KEY` fields are kept
for compatibility but are not used by the current runtime.

**Untested here.** Both paths exist in the code and are described because
nothing else describes the runtime one, but every other claim on this page was
executed and these were not. Treat them as a starting point.

Note that a CLI runtime cannot constrain output — it reports
`constrained=false` and role parsing falls back to markdown scraping, with the
drift rate described above.

## Choosing a model

The measurement above used `llama3.1:8b` and shows what constrained decoding
fixes: **format compliance**. It says nothing about whether an 8B model
produces _good_ analysis — that was not measured, and a small model that emits
perfectly-shaped empty-calorie findings will steer the loop just as
confidently as a large one.

If you have the memory, run the largest model you can. Declare its capability
honestly: an over-declared endpoint gets handed architect work it cannot do,
and the loop has no way to notice.

## Endpoint preflight

`autoctx run` checks every configured OpenAI-compatible agent, explicit role,
and judge endpoint before starting a generation. It verifies that the endpoint
answers, that the effective model appears in `/v1/models`, and that the model
honors the constrained-output schema. Certain configuration failures stop the
run with exit code 2; transient or indeterminate checks are warnings and the
run proceeds.

Context-window length is not part of the OpenAI-compatible model-list response,
so preflight reports no context-window result rather than guessing. If an
endpoint has a non-standard discovery surface or the operator has independently
validated it, `autoctx run ... --skip-preflight` bypasses these checks.

## Offline mode

`AUTOCONTEXT_OFFLINE=1` means one thing:

> The engine never initiates an outbound connection.

Scoped by **who initiates**. Control-plane sync, provider calls, webhook
notifications and fixture downloads are engine-initiated, so they are off. An
operator SSH-ing into the box is operator-initiated, so it is out of scope --
airgapped does not have to mean unreachable.

Connections to an endpoint that is unambiguously on the same host are not
outbound and remain available. The allowlist is intentionally narrow: the exact
`localhost` name and literal loopback addresses (`127.0.0.0/8` and `::1`). A
private-LAN address or hostname is still egress and is refused; the engine does
not perform DNS resolution to decide whether a name happens to resolve locally.

A complete local setup must configure both the agent and the judge. The normal
`judge_provider=auto` fallback is Anthropic unless it inherits a subscription
CLI runtime, so leaving it on `auto` is a startup conflict offline:

```bash
AUTOCONTEXT_OFFLINE=1 \
AUTOCONTEXT_AGENT_PROVIDER=ollama \
AUTOCONTEXT_LOCAL_MODEL=llama3.1:8b \
AUTOCONTEXT_JUDGE_PROVIDER=ollama \
AUTOCONTEXT_JUDGE_BASE_URL=http://localhost:11434/v1 \
  autoctx run my_task
```

Anything blocked raises an error naming what it was, rather than failing as a
timeout three layers down:

```
AUTOCONTEXT_OFFLINE is set; refusing to post a webhook notification
(https://hooks.example/x)
```

### What becomes unavailable

**External runtimes** — `agent_sdk`, `claude-cli`, `codex`, `pi`, `pi-rpc`,
`hermes`, and OpenClaw CLI/factory runtimes — refuse to start. They run code
outside the guarded provider transports, and no amount of guarding this
codebase controls another program's sockets. They are refused rather than
silently trusted, because a guarantee that depends on someone else's behavior
is not a guarantee. An OpenClaw HTTP sidecar remains usable only when its URL is
literal loopback.

Use a local endpoint instead: `ollama`, `vllm`, or `mlx`.

**Configuring egress at the same time is a startup error**, not a silent
precedence rule. Setting `AUTOCONTEXT_OFFLINE=1` alongside
`AUTOCONTEXT_NOTIFY_WEBHOOK_URL`, a remote provider or per-role override, a
remote judge or consultation provider, an SSH/PrimeIntellect executor, a
Hugging Face blob store, or an external runtime fails preflight with every
conflict listed at once. Letting one quietly win would mean you either got a
guarantee you did not receive or a sync you did not expect, decided by load
order.

### How it is enforced, and how far that goes

Two mechanisms, and it is worth knowing which covers what.

A test runs a **complete generation** with a guard installed at
`socket.socket.connect` — below every HTTP client, SDK and transport in the
process — and asserts zero connection attempts. Per-path checks only cover the
paths someone remembered; this covers the path nobody remembered.

A CI guard fails the build when a new `urlopen` appears without a
`require_online` in the same function. That is the part that stops this
decaying: without it the enforcement is a convention held up by code review.
Its limits are written down in `tests/test_offline_mode.py` — it does not catch
egress through an SDK client or a helper one frame down, which is what the
socket-level test is paired with it to cover.

**What this is not is proof.** The strongest assurance comes from outside the
process: run autocontext in a network namespace with no route, or behind a
firewall rule that drops egress. This work is what makes doing that actually
function instead of hanging on a call you did not expect it to make. That claim
you can verify yourself in about thirty seconds, which is worth more than
anything the program could assert about itself.

## Not covered yet

**llama.cpp.** Reachable through `openai-compatible` with
`AUTOCONTEXT_PROVIDER_HOSTING=local`, but not exercised while writing this
page, so it is listed rather than documented.
