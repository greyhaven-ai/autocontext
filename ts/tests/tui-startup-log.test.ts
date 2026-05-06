import { describe, expect, it } from "vitest";

import { buildInitialTuiLogLines } from "../src/tui/startup-log.js";

describe("TUI startup log", () => {
  it("announces the loaded activity settings before command help", () => {
    const lines = buildInitialTuiLogLines({
      serverUrl: "http://127.0.0.1:9000",
      scenarios: ["grid_ctf", "support_triage"],
      activitySettings: {
        filter: "commands",
        verbosity: "quiet",
      },
    });

    expect(lines.slice(0, 3)).toEqual([
      "interactive server: http://127.0.0.1:9000",
      "available scenarios: grid_ctf, support_triage",
      "loaded activity filter=commands verbosity=quiet",
    ]);
    expect(lines).toContain(
      "/activity [status|reset|<all|runtime|prompts|commands|children|errors> [quiet|normal|verbose]]",
    );
  });
});
