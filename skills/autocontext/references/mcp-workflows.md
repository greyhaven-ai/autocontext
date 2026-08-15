# MCP Workflows

When Hermes already has MCP configured, autocontext is reachable as
MCP tools instead of (or alongside) the CLI. Use this only when MCP is
the simpler path; CLI-first remains the default for visibility and
debuggability.

## Setting up the MCP server

```bash
autoctx serve mcp
```

The server speaks MCP on stdio. Add it to your Hermes config under
`mcp_servers` (path varies by Hermes deployment):

```jsonc
{
  "mcp_servers": {
    "autocontext": {
      "command": "autoctx",
      "args": ["serve", "mcp"]
    }
  }
}
```

## Tool name mapping

Each CLI subcommand maps to an `autocontext_*` MCP tool. Examples:

| CLI command                       | MCP tool                      |
| --------------------------------- | ----------------------------- |
| `autoctx judge`                   | `autocontext_judge`           |
| `autoctx improve`                 | `autocontext_improve`         |
| `autoctx list`                    | `autocontext_list_runs`       |
| `autoctx show <run-id>`           | `autocontext_get_run_status`  |
| `autoctx replay <run-id>`         | `autocontext_run_replay`      |

Full list and argument shapes: `autoctx capabilities --json` enumerates
every available MCP tool with its input schema.

## When to prefer CLI over MCP

- The user wants to see exactly what happened (CLI streams to terminal).
- The operation is one-shot, not part of a workflow loop.
- Hermes is not currently configured for MCP.

## When MCP is the better path

- Hermes is already running and has `mcp_autocontext_*` tools loaded.
- The operation is part of an automated multi-step task.
- The agent needs typed input schemas instead of shell parsing.
