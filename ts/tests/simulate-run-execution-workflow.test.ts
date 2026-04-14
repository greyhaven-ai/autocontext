import { describe, expect, it, vi } from "vitest";

import { executeSimulateRunWorkflow } from "../src/cli/simulate-command-workflow.js";

describe("simulate run execution workflow", () => {
  it("executes a run with provider, knowledge root, and parsed numeric options", async () => {
    const provider = { name: "live-provider" };
    const sweep = [{ name: "threshold", values: [0.5, 0.7] }];
    const variables = { threshold: 0.8, budget: 100 };
    const run = vi.fn().mockResolvedValue({ status: "completed", id: "sim_123" });
    const createEngine = vi.fn(() => ({ run }));

    const result = await executeSimulateRunWorkflow({
      description: "simulate a rollback deployment",
      provider,
      knowledgeRoot: "/tmp/knowledge",
      variables,
      sweep,
      runs: "4",
      maxSteps: "12",
      saveAs: "deploy_sim",
      createEngine,
    });

    expect(createEngine).toHaveBeenCalledWith(provider, "/tmp/knowledge");
    expect(run).toHaveBeenCalledWith({
      description: "simulate a rollback deployment",
      variables,
      sweep,
      runs: 4,
      maxSteps: 12,
      saveAs: "deploy_sim",
    });
    expect(result).toEqual({ status: "completed", id: "sim_123" });
  });

  it("preserves optional simulate run inputs when omitted", async () => {
    const provider = { name: "live-provider" };
    const run = vi.fn().mockResolvedValue({ status: "completed" });
    const createEngine = vi.fn(() => ({ run }));

    await executeSimulateRunWorkflow({
      description: "simulate a rollback deployment",
      provider,
      knowledgeRoot: "/tmp/knowledge",
      createEngine,
    });

    expect(run).toHaveBeenCalledWith({
      description: "simulate a rollback deployment",
      variables: undefined,
      sweep: undefined,
      runs: undefined,
      maxSteps: undefined,
      saveAs: undefined,
    });
  });
});
