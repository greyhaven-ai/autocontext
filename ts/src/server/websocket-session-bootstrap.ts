import {
  BASE_SERVER_CAPABILITIES,
  PROTOCOL_VERSION,
  TRANSCRIPT_SERVER_CAPABILITIES,
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
    minimum_generations: state.minimumGenerations ?? undefined,
    generations: state.targetGenerations ?? undefined,
    phase: state.phase ?? undefined,
  };
}

export function buildHelloMessage(
  opts: { runTranscript?: boolean; capabilities?: readonly string[] } = {},
): ServerMessage {
  const capabilities = opts.runTranscript
    ? [
        ...TRANSCRIPT_SERVER_CAPABILITIES,
        ...BASE_SERVER_CAPABILITIES,
        ...(opts.capabilities ?? []),
      ]
    : [...BASE_SERVER_CAPABILITIES];
  return {
    type: "hello",
    protocol_version: PROTOCOL_VERSION,
    ...(opts.runTranscript
      ? { transcript_protocol_version: TRANSCRIPT_PROTOCOL_VERSION }
      : {}),
    capabilities: [...new Set(capabilities)],
  };
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
