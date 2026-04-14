import { describe, expect, it } from "vitest";

import {
  buildHeadlessTuiOutput,
  buildInteractiveTuiRequest,
  planTuiCommand,
  TUI_HELP_TEXT,
} from "../src/cli/tui-command-workflow.js";

describe("tui command workflow", () => {
  it("exposes stable help text", () => {
    expect(TUI_HELP_TEXT).toContain("autoctx tui");
    expect(TUI_HELP_TEXT).toContain("--port 8000");
    expect(TUI_HELP_TEXT).toContain("--headless");
  });

  it("plans TUI startup with headless TTY fallback", () => {
    expect(planTuiCommand({ port: undefined, headless: false }, false)).toEqual({
      port: 8000,
      headless: true,
    });
    expect(planTuiCommand({ port: "9000", headless: false }, true)).toEqual({
      port: 9000,
      headless: false,
    });
    expect(planTuiCommand({ port: "9100", headless: true }, true)).toEqual({
      port: 9100,
      headless: true,
    });
  });

  it("renders headless startup output", () => {
    expect(
      buildHeadlessTuiOutput({
        serverUrl: "http://127.0.0.1:9000",
        scenarios: ["grid_ctf", "othello"],
      }),
    ).toEqual([
      "autocontext interactive server listening at http://127.0.0.1:9000",
      "Scenarios: grid_ctf, othello",
    ]);
  });

  it("builds interactive TUI render requests", () => {
    const manager = { kind: "manager" };
    expect(
      buildInteractiveTuiRequest({
        manager,
        serverUrl: "http://127.0.0.1:9000",
      }),
    ).toEqual({
      manager,
      serverUrl: "http://127.0.0.1:9000",
    });
  });
});
