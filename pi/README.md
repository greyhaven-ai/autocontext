# pi-autocontext

autocontext extension for [Pi coding agent](https://github.com/earendil-works/pi) — iterative strategy generation, LLM judging, and evaluation tools.

## Install

```bash
pi install npm:pi-autocontext@0.2.5
```

Current package note: `pi-autocontext@0.2.5` is on a separate Pi extension line and currently depends on `autoctx@^0.5.1`. Use it for Pi tools/skills, and install `autocontext==0.6.0` or `autoctx@0.6.0` directly when you need 0.6 runtime features until the next Pi extension release updates its bundled TypeScript dependency.

Or add to your project's `.pi/settings.json`:

```json
{
  "packages": ["npm:pi-autocontext@0.2.5"]
}
```

## What You Get

### Tools

| Tool | Description |
|------|-------------|
| `autocontext_judge` | Evaluate agent output against a rubric using LLM-based judging |
| `autocontext_improve` | Run multi-round improvement loop with judge feedback |
| `autocontext_status` | Check status of autocontext runs and tasks |
| `autocontext_scenarios` | List available evaluation scenarios and families |
| `autocontext_queue` | Enqueue a task for background evaluation |
| `autocontext_runtime_snapshot` | Inspect run artifacts, package provenance, compaction ledger entries, session branch lineage, and recent events |

### Skills

- **`/skill:autocontext`** — Full instructions for using autocontext tools, running evaluations, and interpreting results

### Prompt Templates

- **`/autoctx-status`** — Quick project status check

## Usage

Once installed, the tools are available to the LLM automatically. You can also invoke them directly:

```
> Evaluate the quality of this code against our coding standards rubric
> Run an improvement loop on this draft with max 5 rounds
> Show me the status of recent autocontext runs
> Inspect the runtime snapshot for run-123 and session sess-123
> List available evaluation scenarios
```

Or use the skill for guided workflows:

```
/skill:autocontext
```

## Requirements

- [Pi coding agent](https://github.com/earendil-works/pi)
- An LLM provider configured in Pi (Anthropic, OpenAI, etc.)
- Optional: `autoctx` CLI for standalone usage outside Pi

## Configuration

The extension auto-discovers your autocontext configuration:

- **Provider**: Uses Pi's configured LLM provider
- **Database**: Looks for `runs/autocontext.sqlite3` or `AUTOCONTEXT_DB_PATH` env var
- **Events**: Reads `runs/events.ndjson` or `AUTOCONTEXT_EVENT_STREAM_PATH` for recent runtime events
- **Scenarios**: Discovers registered scenarios from the `autoctx` package

## Links

- [autocontext](https://github.com/greyhaven-ai/autocontext) — Main repository
- [autoctx on npm](https://www.npmjs.com/package/autoctx) — Core TypeScript package
- [Pi coding agent](https://github.com/earendil-works/pi) — The Pi agent
