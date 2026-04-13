import { readFileSync } from "node:fs";
import { join } from "node:path";
import { describe, expect, it } from "vitest";

import { SandboxManager } from "../src/execution/sandbox.js";

const PROVIDER_STUB = {
  name: "test-provider",
  defaultModel: () => "test-model",
  complete: async () => ({ text: "{}", usage: {} }),
};

describe("SandboxManager encapsulation", () => {
  it("uses real #private fields for internal sandbox state", () => {
    const source = readFileSync(
      join(import.meta.dirname, "..", "src", "execution", "sandbox.ts"),
      "utf-8",
    );

    expect(source).toContain("#provider");
    expect(source).toContain("#store");
    expect(source).toContain("#runsRoot");
    expect(source).toContain("#knowledgeRoot");
    expect(source).toContain("#maxSandboxes");
    expect(source).toContain("#sandboxes");
    expect(source).not.toContain("private provider:");
    expect(source).not.toContain("private store:");
    expect(source).not.toContain("private runsRoot:");
    expect(source).not.toContain("private knowledgeRoot:");
    expect(source).not.toContain("private maxSandboxes:");
  });
});

describe("SandboxManager", () => {
  it("creates, lists, reports, and destroys sandboxes", () => {
    const manager = new SandboxManager({
      provider: PROVIDER_STUB,
      store: {} as never,
      runsRoot: "/tmp/runs",
      knowledgeRoot: "/tmp/knowledge",
      maxSandboxes: 2,
    });

    const created = manager.create("grid_ctf", "test-user");

    expect(created.scenarioName).toBe("grid_ctf");
    expect(created.userId).toBe("test-user");
    expect(created.status).toBe("active");
    expect(manager.getStatus(created.sandboxId)).toEqual(created);
    expect(manager.list()).toEqual([created]);

    expect(manager.destroy(created.sandboxId)).toBe(true);
    expect(manager.getStatus(created.sandboxId)).toBeNull();
    expect(manager.list()).toEqual([]);
  });

  it("rejects unknown scenarios and enforces the configured sandbox limit", () => {
    const manager = new SandboxManager({
      provider: PROVIDER_STUB,
      store: {} as never,
      runsRoot: "/tmp/runs",
      knowledgeRoot: "/tmp/knowledge",
      maxSandboxes: 1,
    });

    expect(() => manager.create("missing_scenario")).toThrow(/Unknown scenario/);

    manager.create("grid_ctf", "user-a");
    expect(() => manager.create("grid_ctf", "user-b")).toThrow(/Maximum sandbox limit/);
  });
});
