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
      active: false,
      paused: false,
      scenario: null,
      generation: undefined,
      phase: undefined,
    });
  });

  it("omits the default floor for protocol-v2 compatibility and exposes non-default floors", () => {
    expect(buildStateMessage({
      ...state,
      active: true,
      minimumGenerations: 1,
      targetGenerations: 4,
    })).not.toHaveProperty("minimum_generations");
    expect(buildStateMessage({
      ...state,
      active: true,
      minimumGenerations: 2,
      targetGenerations: 4,
    })).toMatchObject({
      minimum_generations: 2,
      generations: 4,
    });
  });

  it("builds the initial websocket bootstrap sequence in protocol order", () => {
    expect(buildSessionBootstrapMessages(environment, state)).toEqual([
      {
        type: "hello",
        protocol_version: 2,
        capabilities: [
          "structured_task_creation_v1",
          "agent_task_outcome_v1",
          "minimum_iterations_v1",
        ],
      },
      {
        type: "environments",
        scenarios: [{ name: "grid_ctf", description: "Capture the flag" }],
        executors: [{ mode: "local", available: true, description: "Local executor" }],
        current_executor: "local",
        agent_provider: "deterministic",
      },
      {
        type: "state",
        active: false,
        paused: false,
        scenario: null,
        generation: undefined,
        phase: undefined,
      },
    ]);
  });

  it("adds transcript-only capabilities after explicit opt-in", () => {
    expect(buildSessionBootstrapMessages(environment, state, { runTranscript: true })).toEqual([
      {
        type: "hello",
        protocol_version: 2,
        transcript_protocol_version: 1,
        capabilities: [
          "run_transcript_v1",
          "safe_run_stop_v1",
          "agent_task_plan_v1",
          "agent_progress_notes_v1",
          "structured_task_creation_v1",
          "agent_task_outcome_v1",
          "minimum_iterations_v1",
        ],
      },
      {
        type: "environments",
        scenarios: [{ name: "grid_ctf", description: "Capture the flag" }],
        executors: [{ mode: "local", available: true, description: "Local executor" }],
        current_executor: "local",
        agent_provider: "deterministic",
      },
      {
        type: "state",
        active: false,
        paused: false,
        scenario: null,
        generation: undefined,
        phase: undefined,
      },
    ]);
  });
});
