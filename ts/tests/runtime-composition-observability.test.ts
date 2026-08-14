import { describe, expect, it } from "vitest";

import { activateRuntimeComponent } from "../src/runtimes/component-lifecycle.js";
import { RuntimeCompositionInventory } from "../src/runtimes/composition-observability.js";
import {
  createInMemoryWorkspaceEnv,
  defineRuntimeCommand,
  type RuntimeToolGrant,
} from "../src/runtimes/workspace-env.js";

describe("runtime composition observability", () => {
  it("tracks scoped workspace capabilities and revokes retained handles on disposal", async () => {
    const inventory = new RuntimeCompositionInventory();
    const scope = await activateRuntimeComponent(
      { componentId: "extension:workspace-owner" },
      () => undefined,
    );
    const tool: RuntimeToolGrant = {
      kind: "tool",
      name: "lookup",
      execute: async () => ({ text: "found" }),
    };
    const workspace = await createInMemoryWorkspaceEnv({ cwd: "/workspace" }).scope({
      componentScope: scope,
      compositionInventory: inventory,
      commands: [defineRuntimeCommand("inspect", () => ({
        stdout: "ready",
        stderr: "",
        exitCode: 0,
      }))],
      tools: [tool],
    });
    const retainedTool = workspace.tools?.[0];

    expect(inventory.snapshot().registrations).toEqual([
      {
        componentId: "extension:workspace-owner",
        kind: "grant",
        observationalIdentity: "command:inspect",
      },
      {
        componentId: "extension:workspace-owner",
        kind: "tool",
        observationalIdentity: "lookup",
      },
    ]);
    await expect(workspace.exec("inspect")).resolves.toMatchObject({ stdout: "ready" });
    await expect(retainedTool?.execute?.({})).resolves.toEqual({ text: "found" });

    await scope.dispose();

    expect(inventory.snapshot().registrations).toEqual([]);
    await expect(workspace.exec("inspect")).rejects.toThrow("workspace is unavailable");
    await expect(retainedTool?.execute?.({})).rejects.toThrow("workspace is unavailable");
    await expect(workspace.writeFile("stale.txt", "nope")).rejects.toThrow(
      "workspace is unavailable",
    );
    expect(() => workspace.tools).toThrow("workspace is unavailable");
  });

  it("rejects inventory-owned grants without an owning component", async () => {
    const inventory = new RuntimeCompositionInventory();

    await expect(createInMemoryWorkspaceEnv().scope({
      compositionInventory: inventory,
      commands: [defineRuntimeCommand("inspect", () => ({
        stdout: "ready",
        stderr: "",
        exitCode: 0,
      }))],
    })).rejects.toThrow("requires a component scope");
    expect(inventory.snapshot().registrations).toEqual([]);
  });
});
