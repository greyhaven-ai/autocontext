import { describe, expect, it } from "vitest";

import {
  planServeCommand,
  renderServeStartup,
  SERVE_HELP_TEXT,
} from "../src/cli/serve-command-workflow.js";

describe("serve command workflow", () => {
  it("exposes stable help text", () => {
    expect(SERVE_HELP_TEXT).toContain("autoctx serve");
    expect(SERVE_HELP_TEXT).toContain("--port");
    expect(SERVE_HELP_TEXT).toContain("--host");
    expect(SERVE_HELP_TEXT).toContain("--json");
  });

  it("plans serve options with defaults", () => {
    expect(planServeCommand({ port: undefined, host: undefined, json: false })).toEqual({
      port: 8000,
      host: "127.0.0.1",
      json: false,
    });
  });

  it("plans serve options from explicit values", () => {
    expect(planServeCommand({ port: "9000", host: "0.0.0.0", json: true })).toEqual({
      port: 9000,
      host: "0.0.0.0",
      json: true,
    });
  });

  it("renders machine-readable startup output", () => {
    expect(
      renderServeStartup(
        {
          url: "http://127.0.0.1:9000",
          apiUrl: "http://127.0.0.1:9000/api/runs",
          wsUrl: "ws://127.0.0.1:9000/ws/interactive",
          host: "127.0.0.1",
          port: 9000,
          scenarios: ["grid_ctf", "othello"],
        },
        true,
      ),
    ).toEqual([
      JSON.stringify(
        {
          url: "http://127.0.0.1:9000",
          apiUrl: "http://127.0.0.1:9000/api/runs",
          wsUrl: "ws://127.0.0.1:9000/ws/interactive",
          host: "127.0.0.1",
          port: 9000,
          scenarios: ["grid_ctf", "othello"],
        },
      ),
    ]);
  });

  it("renders human-readable startup output", () => {
    expect(
      renderServeStartup(
        {
          url: "http://127.0.0.1:9000",
          apiUrl: "http://127.0.0.1:9000/api/runs",
          wsUrl: "ws://127.0.0.1:9000/ws/interactive",
          host: "127.0.0.1",
          port: 9000,
          scenarios: ["grid_ctf", "othello"],
        },
        false,
      ),
    ).toEqual([
      "autocontext server listening at http://127.0.0.1:9000",
      "API: http://127.0.0.1:9000/api/runs",
      "WebSocket: ws://127.0.0.1:9000/ws/interactive",
      "Scenarios: grid_ctf, othello",
    ]);
  });
});
