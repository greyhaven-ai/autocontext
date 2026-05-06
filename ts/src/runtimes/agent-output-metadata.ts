import type { AgentOutput } from "./base.js";

export interface AgentOutputMetadataOptions {
  operation?: string;
  runtimeSessionId?: string;
}

export function agentOutputMetadata(
  runtimeName: string,
  output: AgentOutput,
  options: AgentOutputMetadataOptions = {},
): Record<string, unknown> {
  const metadata: Record<string, unknown> = { ...(output.metadata ?? {}), runtime: runtimeName };
  if (options.operation !== undefined) metadata.operation = options.operation;
  if (options.runtimeSessionId !== undefined) metadata.runtimeSessionId = options.runtimeSessionId;
  if (output.model !== undefined) metadata.model = output.model;
  if (output.sessionId !== undefined) metadata.agentRuntimeSessionId = output.sessionId;
  if (output.costUsd !== undefined) metadata.costUsd = output.costUsd;
  if (output.structured !== undefined) metadata.structured = output.structured;
  return metadata;
}
