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
    ...(environment.routingContext ? { routing_context: environment.routingContext } : {}),
  };
}

export function buildStateMessage(state: RunManagerState): ServerMessage {
  return {
    type: "state",
    active: state.active,
    paused: state.paused,
    scenario: state.scenario,
    generation: state.generation ?? undefined,
    phase: state.phase ?? undefined,
  };
}

export function buildHelloMessage(
  opts: { runTranscript?: boolean; capabilities?: readonly string[] } = {},
): ServerMessage {
  return opts.runTranscript
    ? {
        type: "hello",
        protocol_version: PROTOCOL_VERSION,
        transcript_protocol_version: TRANSCRIPT_PROTOCOL_VERSION,
        capabilities: [...new Set([...SERVER_CAPABILITIES, ...(opts.capabilities ?? [])])],
      }
    : { type: "hello", protocol_version: PROTOCOL_VERSION };
}

export function buildSessionBootstrapMessages(
  environment: EnvironmentInfo,
  state: RunManagerState,
  opts: { runTranscript?: boolean; capabilities?: readonly string[] } = {},
): ServerMessage[] {
  return [
    buildHelloMessage(opts),
    buildEnvironmentMessage(environment),
    buildStateMessage(state),
  ];
}
