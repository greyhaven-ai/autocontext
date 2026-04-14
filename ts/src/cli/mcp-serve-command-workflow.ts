export const MCP_SERVE_HELP_TEXT = `autoctx mcp-serve — Start MCP server on stdio

Starts the Model Context Protocol server on stdio for integration with
Claude Code, Cursor, and other MCP-compatible editors.

Core exported tools:
  evaluate_output       Evaluate output against a rubric
  run_improvement_loop  Multi-round improvement loop
  queue_task            Enqueue a task for background evaluation
  get_queue_status      Check task queue status
  list_runs             List recent runs
  get_run_status        Get detailed run status
  run_replay            Replay a generation
  list_scenarios        List available scenarios
  export_package        Export strategy package data
  create_agent_task     Create a saved agent-task scenario

Additional tools cover playbooks, sandboxing, tournaments, and package import/export.

Transport: stdio (JSON-RPC over stdin/stdout)

See also: serve, judge, improve`;

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
