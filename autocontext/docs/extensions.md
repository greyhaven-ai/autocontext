# Extension Hooks

Autocontext exposes a small Python extension hook bus for Pi-shaped runtime customization. It is intentionally narrow: extensions receive structured events at stable runtime boundaries and may mutate the event payload, block the operation, or record side metadata.

Load extensions with `AUTOCONTEXT_EXTENSIONS`:

```bash
AUTOCONTEXT_EXTENSIONS=my_project.autoctx_hooks,./local_hooks.py \
uv run autoctx run --scenario grid_ctf --gens 3
```

Set `AUTOCONTEXT_EXTENSION_FAIL_FAST=true` when hook failures should stop the run. By default, hook handler exceptions are recorded on the event and the run continues.

## Extension Shape

An extension module may expose `register(api)`, `configure(api)`, or `setup(api)`. References may also target a callable directly with `module:function`.

```python
from autocontext.extensions import HookEvents, HookResult


def register(api):
    @api.on(HookEvents.CONTEXT)
    def add_competitor_context(event):
        roles = dict(event.payload["roles"])
        roles["competitor"] += "\nPrefer concise, testable strategies."
        return HookResult(payload={"roles": roles})
```

Handlers may mutate `event.payload` in place, return a `dict` to merge into the payload, or return `HookResult` for explicit replacement/blocking:

```python
return HookResult(block=True, reason="extension policy rejected this artifact")
```

## Built-In Events

| Event | Payload |
| --- | --- |
| `run_start` | `run_id`, `scenario`, `target_generations`, `loaded_extensions` |
| `run_end` | `run_id`, `scenario`, `status`, summary metrics, optional `error` |
| `generation_start` | `run_id`, `scenario`, `generation` |
| `generation_end` | `run_id`, `scenario`, `generation`, `status`, metrics, optional `error` |
| `context_components` | Prompt assembly inputs before prompt construction |
| `before_compaction` | Semantic compaction components before compaction |
| `after_compaction` | Semantic compaction inputs and compacted components |
| `context` | Final role prompts as `roles.competitor`, `roles.analyst`, `roles.coach`, `roles.architect` |
| `before_provider_request` | Provider, role, model, prompt or messages, token and temperature settings |
| `after_provider_response` | Provider, role, request, text, usage, metadata |
| `before_judge` | Judge prompt, model, temperature, sample and retry metadata |
| `after_judge` | Judge request and raw `response_text` before parsing |
| `artifact_write` | Path, format, content or payload, append/buffered metadata |

`artifact_write` hooks may rewrite `path`, but ArtifactStore writes must stay
inside the original managed root (`runs`, `knowledge`, `skills`, or
`.claude/skills`).

## Design Notes

The hook bus follows the same spirit as Pi extensions: small contracts, ordered handlers, branch/run-safe payloads, and no hidden prompt parsing. Autocontext keeps its full control plane by default; use hooks for local policy, observability, context shaping, and Pi-like harness adaptation.
