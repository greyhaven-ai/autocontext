import { describe, it, expect } from "vitest";
import { StartRunCmdSchema } from "../src/server/protocol.js";

describe("StartRunCmd curator_approval_mode", () => {
  it("accepts a mode", () => {
    const p = StartRunCmdSchema.parse({
      type: "start_run",
      scenario: "grid_ctf",
      generations: 3,
      curator_approval_mode: "approve",
    });
    expect(p.curator_approval_mode).toBe("approve");
  });
  it("defaults to auto when omitted", () => {
    const p = StartRunCmdSchema.parse({ type: "start_run", scenario: "grid_ctf", generations: 3 });
    expect(p.curator_approval_mode).toBe("auto");
  });
});
