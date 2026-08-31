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
    expect(TUI_HELP_TEXT).toContain("--admin");
  });

  it("plans TUI startup with headless TTY fallback", () => {
    expect(planTuiCommand({ port: undefined, headless: false }, false)).toEqual({
      port: 8000,
      headless: true,
      admin: false,
    });
    expect(planTuiCommand({ port: "9000", headless: false }, true)).toEqual({
      port: 9000,
      headless: false,
      admin: false,
    });
    expect(planTuiCommand({ port: "9100", headless: true }, true)).toEqual({
      port: 9100,
      headless: true,
      admin: false,
    });
    expect(planTuiCommand({ port: "9200", admin: true }, true)).toMatchObject({
      port: 9200,
      admin: true,
    });
  });

  it("rejects invalid or partially parsed ports", () => {
    expect(() => planTuiCommand({ port: "0" }, true)).toThrow("1 through 65535");
    expect(() => planTuiCommand({ port: "8000oops" }, true)).toThrow("1 through 65535");
    expect(() => planTuiCommand({ port: "65536" }, true)).toThrow("1 through 65535");
  });

  it("rejects remote attach when output is headless or not a TTY", () => {
    expect(() => planTuiCommand({ connect: "https://example.test", headless: true }, true))
      .toThrow("requires an interactive TTY");
    expect(() => planTuiCommand({ connect: "https://example.test" }, false))
      .toThrow("requires an interactive TTY");
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
