import { describe, expect, it, vi } from "vitest";

import { formatCommandHelp, handleInteractiveTuiCommand } from "../src/tui/commands.js";

describe("TUI command help", () => {
  it("uses the same plain-language concepts as the CLI contract", () => {
    const help = formatCommandHelp().join("\n");

    expect(help).toContain('/solve "plain-language goal"');
    expect(help).toContain("/run <scenario> [iterations]");
    expect(help).toContain("/status <run-id>");
    expect(help).toContain("/show <run-id> --best");
    expect(help).toContain("/watch <run-id>");
  });

  it("turns /solve plain language into scenario creation and a run", async () => {
    const manager = {
      createScenario: vi.fn(async () => ({ name: "orbital_transfer" })),
      confirmScenario: vi.fn(async () => ({ name: "orbital_transfer", testScores: [] })),
      startRun: vi.fn(async () => "run-123"),
    };

    const result = await handleInteractiveTuiCommand({
      manager: manager as never,
      configDir: ".",
      raw: '/solve "build an orbital transfer optimizer"',
      pendingLogin: null,
    });

    expect(manager.createScenario).toHaveBeenCalledWith("build an orbital transfer optimizer");
    expect(manager.startRun).toHaveBeenCalledWith("orbital_transfer", 5);
    expect(result.logLines).toContain("accepted run run-123");
  });
});
