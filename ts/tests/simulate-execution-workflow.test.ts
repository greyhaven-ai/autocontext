import { describe, expect, it, vi } from "vitest";

import {
  createCompareProvider,
  createReplayProvider,
  executeSimulateCompareWorkflow,
  executeSimulateReplayWorkflow,
} from "../src/cli/simulate-command-workflow.js";

describe("simulate execution workflow", () => {
  it("creates stable local compare and replay providers", () => {
    expect(createCompareProvider()).toEqual({ name: "local-compare" });
    expect(createReplayProvider()).toEqual({ name: "local-replay" });
  });

  it("executes compare workflow with compare ids", async () => {
    const compare = vi.fn().mockResolvedValue({ status: "completed", summary: "ok" });
    const createEngine = vi.fn(() => ({ compare }));

    const result = await executeSimulateCompareWorkflow({
      compareLeft: "sim_a",
      compareRight: "sim_b",
      knowledgeRoot: "/tmp/knowledge",
      createEngine,
    });

    expect(createEngine).toHaveBeenCalledWith({ name: "local-compare" }, "/tmp/knowledge");
    expect(compare).toHaveBeenCalledWith({ left: "sim_a", right: "sim_b" });
    expect(result).toEqual({ status: "completed", summary: "ok" });
  });

  it("executes replay workflow with parsed variables and max steps", async () => {
    const replay = vi.fn().mockResolvedValue({ status: "completed", summary: { score: 0.8 } });
    const createEngine = vi.fn(() => ({ replay }));
    const parseVariableOverrides = vi.fn(() => ({ threshold: 0.9 }));

    const result = await executeSimulateReplayWorkflow({
      replayId: "deploy_sim",
      knowledgeRoot: "/tmp/knowledge",
      variables: "threshold=0.9",
      maxSteps: "12",
      createEngine,
      parseVariableOverrides,
    });

    expect(createEngine).toHaveBeenCalledWith({ name: "local-replay" }, "/tmp/knowledge");
    expect(parseVariableOverrides).toHaveBeenCalledWith("threshold=0.9");
    expect(replay).toHaveBeenCalledWith({
      id: "deploy_sim",
      variables: { threshold: 0.9 },
      maxSteps: 12,
    });
    expect(result).toEqual({ status: "completed", summary: { score: 0.8 } });
  });

  it("executes replay workflow without optional inputs", async () => {
    const replay = vi.fn().mockResolvedValue({ status: "completed" });
    const createEngine = vi.fn(() => ({ replay }));
    const parseVariableOverrides = vi.fn();

    await executeSimulateReplayWorkflow({
      replayId: "deploy_sim",
      knowledgeRoot: "/tmp/knowledge",
      createEngine,
      parseVariableOverrides,
    });

    expect(parseVariableOverrides).not.toHaveBeenCalled();
    expect(replay).toHaveBeenCalledWith({
      id: "deploy_sim",
      variables: undefined,
      maxSteps: undefined,
    });
  });
});
