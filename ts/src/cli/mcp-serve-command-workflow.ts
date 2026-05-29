/**
 * PR #1001 review (P3): the help text was hard-coded to
 * `autoctx mcp-serve`, which leaked into `autoctx serve mcp --help`
 * after slice 6 added the canonical sub-command path. The builder
 * lets each entry point render help under its own command name
 * without duplicating the body, so the canonical and legacy
 * surfaces stay byte-identical except for the header. Same pattern
 * as PR #999's `buildScenarioHelpText`.
 */
export function buildMcpServeHelpText(commandName: string): string {
  return `autoctx ${commandName} — Start MCP server on stdio

Starts the Model Context Protocol server on stdio for integration with
Claude Code, Cursor, and other MCP-compatible editors.

Core exported tools:
  evaluate_output       Evaluate output against a rubric
  run_improvement_loop  Multi-round improvement loop
  queue_task            Enqueue a task for background evaluation
  get_queue_status      Check task queue status
  list_runs             List recent runs
  get_run_status        Get detailed run status
  list_runtime_sessions List recorded runtime sessions
  get_runtime_session   Inspect a runtime session by session id or run id
  get_runtime_session_timeline Inspect a runtime-session timeline by session id or run id
  run_replay            Replay a generation
  list_scenarios        List available scenarios
  export_package        Export strategy package data
  create_agent_task     Create a saved agent-task scenario

Additional tools cover playbooks, sandboxing, tournaments, and package import/export.

Transport: stdio (JSON-RPC over stdin/stdout)

See also: serve, judge, improve`;
}

export const MCP_SERVE_HELP_TEXT = buildMcpServeHelpText("mcp-serve");
export const SERVE_MCP_HELP_TEXT = buildMcpServeHelpText("serve mcp");

export function buildMcpServeRequest<TStore, TProvider>(input: {
  store: TStore;
  provider: TProvider;
  model: string;
  dbPath: string;
  runsRoot: string;
  knowledgeRoot: string;
}): {
  store: TStore;
  provider: TProvider;
  model: string;
  dbPath: string;
  runsRoot: string;
  knowledgeRoot: string;
} {
  return {
    store: input.store,
    provider: input.provider,
    model: input.model,
    dbPath: input.dbPath,
    runsRoot: input.runsRoot,
    knowledgeRoot: input.knowledgeRoot,
  };
}
