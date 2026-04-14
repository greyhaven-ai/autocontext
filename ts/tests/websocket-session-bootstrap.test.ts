import { describe, expect, it } from "vitest";

import {
  buildEnvironmentMessage,
  buildSessionBootstrapMessages,
  buildStateMessage,
} from "../src/server/websocket-session-bootstrap.js";

describe("websocket session bootstrap", () => {
  const environment = {
    scenarios: [{ name: "grid_ctf", description: "Capture the flag" }],
    executors: [{ mode: "local", available: true, description: "Local executor" }],
    currentExecutor: "local",
    agentProvider: "deterministic",
  };

  const state = {
    active: false,
    paused: false,
    runId: null,
    scenario: null,
    generation: null,
    phase: null,
  };

  it("builds the environment message from run-manager environment info", () => {
    expect(buildEnvironmentMessage(environment)).toEqual({
      type: "environments",
      scenarios: [{ name: "grid_ctf", description: "Capture the flag" }],
      executors: [{ mode: "local", available: true, description: "Local executor" }],
      current_executor: "local",
      agent_provider: "deterministic",
    });
  });

  it("builds the state message from run-manager state", () => {
    expect(buildStateMessage(state)).toEqual({
      type: "state",
      paused: false,
      generation: undefined,
      phase: undefined,
    });
  });

  it("builds the initial websocket bootstrap sequence in protocol order", () => {
    expect(buildSessionBootstrapMessages(environment, state)).toEqual([
      { type: "hello", protocol_version: 1 },
      {
        type: "environments",
        scenarios: [{ name: "grid_ctf", description: "Capture the flag" }],
        executors: [{ mode: "local", available: true, description: "Local executor" }],
        current_executor: "local",
        agent_provider: "deterministic",
      },
      {
        type: "state",
        paused: false,
        generation: undefined,
        phase: undefined,
      },
    ]);
  });
});
