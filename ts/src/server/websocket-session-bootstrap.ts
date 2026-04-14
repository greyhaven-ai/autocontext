import { PROTOCOL_VERSION, type ServerMessage } from "./protocol.js";
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
): ServerMessage[] {
  return [
    { type: "hello", protocol_version: PROTOCOL_VERSION },
    buildEnvironmentMessage(environment),
    buildStateMessage(state),
  ];
}
