import { describe, expect, it } from "vitest";

import {
  createDefaultGondolinSandboxPolicy,
  type GondolinBackend,
} from "../src/execution/gondolin-contract.js";

describe("gondolin contract", () => {
  it("defaults to deny-by-default network and secret policy", () => {
    const policy = createDefaultGondolinSandboxPolicy();

    expect(policy.allowNetwork).toBe(false);
    expect(policy.allowedEgressHosts).toEqual([]);
    expect(policy.secrets).toEqual([]);
  });

  it("lets out-of-tree backends implement the execution contract", async () => {
    const backend = {
      execute: async (request) => ({
        result: { score: 1, scenario: request.scenarioName },
        replay: { seed: request.seed },
        stdout: "ok",
      }),
    } satisfies GondolinBackend;

    await expect(
      backend.execute({
        scenarioName: "grid_ctf",
        strategy: { move: "north" },
        seed: 7,
        policy: createDefaultGondolinSandboxPolicy({
          secrets: [{ name: "judge-api-key", envVar: "AUTOCONTEXT_JUDGE_API_KEY" }],
        }),
      }),
    ).resolves.toEqual({
      result: { score: 1, scenario: "grid_ctf" },
      replay: { seed: 7 },
      stdout: "ok",
    });
  });
});
