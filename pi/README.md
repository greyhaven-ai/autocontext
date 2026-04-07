# pi-autocontext

Autocontext extension for [Pi coding agent](https://github.com/badlogic/pi-mono) — iterative strategy generation, LLM judging, and evaluation tools.

## Install

```bash
pi install npm:pi-autocontext
```

Or add to your project's `.pi/settings.json`:

```json
{
  "packages": ["npm:pi-autocontext"]
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
> List available evaluation scenarios
```

Or use the skill for guided workflows:

```
/skill:autocontext
```

## Requirements

- [Pi coding agent](https://github.com/badlogic/pi-mono)
- An LLM provider configured in Pi (Anthropic, OpenAI, etc.)
- Optional: `autoctx` CLI for standalone usage outside Pi

## Configuration

The extension auto-discovers your autocontext configuration:

- **Provider**: Uses Pi's configured LLM provider
- **Database**: Looks for `runs/autocontext.sqlite3` or `AUTOCONTEXT_DB_PATH` env var
- **Scenarios**: Discovers registered scenarios from the `autoctx` package

## Links

- [autocontext](https://github.com/greyhaven-ai/autocontext) — Main repository
- [autoctx on npm](https://www.npmjs.com/package/autoctx) — Core TypeScript package
- [Pi coding agent](https://github.com/badlogic/pi-mono) — The Pi agent
