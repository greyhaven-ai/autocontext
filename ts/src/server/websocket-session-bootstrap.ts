import {
  PROTOCOL_VERSION,
  SERVER_CAPABILITIES,
  TRANSCRIPT_PROTOCOL_VERSION,
  type ServerMessage,
} from "./protocol.js";
import type { EnvironmentInfo, RunManagerState } from "./run-manager.js";

export function buildEnvironmentMessage(environment: EnvironmentInfo): ServerMessage {
  return {
    type: "environments",
    scenarios: environment.scenarios,
    executors: environment.executors,
    current_executor: environment.currentExecutor,
    agent_provider: environment.agentProvider,
  };
}

export function buildStateMessage(state: RunManagerState): ServerMessage {
  return {
    type: "state",
    paused: state.paused,
    generation: state.generation ?? undefined,
    phase: state.phase ?? undefined,
  };
}

export function buildSessionBootstrapMessages(
  environment: EnvironmentInfo,
  state: RunManagerState,
  opts: { runTranscript?: boolean } = {},
): ServerMessage[] {
  return [
    opts.runTranscript
      ? {
          type: "hello",
          protocol_version: PROTOCOL_VERSION,
          transcript_protocol_version: TRANSCRIPT_PROTOCOL_VERSION,
          capabilities: [...SERVER_CAPABILITIES],
        }
      : { type: "hello", protocol_version: PROTOCOL_VERSION },
    buildEnvironmentMessage(environment),
    buildStateMessage(state),
  ];
}
