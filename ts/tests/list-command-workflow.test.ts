import { describe, expect, it, vi } from "vitest";

import {
  executeListCommandWorkflow,
  LIST_HELP_TEXT,
  planListCommand,
  renderListRuns,
} from "../src/cli/list-command-workflow.js";

describe("list command workflow", () => {
  it("exposes stable help text", () => {
    expect(LIST_HELP_TEXT).toContain("autoctx list");
    expect(LIST_HELP_TEXT).toContain("--limit");
    expect(LIST_HELP_TEXT).toContain("--scenario");
    expect(LIST_HELP_TEXT).toContain("--json");
  });

  it("plans list command values with parsed limit and filters", () => {
    expect(
      planListCommand({ limit: "25", scenario: "grid_ctf", json: true }),
    ).toEqual({ limit: 25, scenario: "grid_ctf", json: true });
  });

  it("defaults list command limit to 50", () => {
    expect(
      planListCommand({ limit: undefined, scenario: undefined, json: false }),
    ).toEqual({ limit: 50, scenario: undefined, json: false });
  });

  it("renders empty list output", () => {
    expect(renderListRuns([], false)).toBe("No runs found.");
  });

  it("renders list output as json when requested", () => {
    expect(
      renderListRuns(
        [
          {
            run_id: "run-1",
            scenario: "grid_ctf",
            status: "completed",
            created_at: "2026-04-10T00:00:00Z",
          },
        ],
        true,
      ),
    ).toBe(
      JSON.stringify(
        [
          {
            run_id: "run-1",
            scenario: "grid_ctf",
            status: "completed",
            created_at: "2026-04-10T00:00:00Z",
          },
        ],
        null,
        2,
      ),
    );
  });

  it("renders list output as human-readable rows", () => {
    expect(
      renderListRuns(
        [
          {
            run_id: "run-1",
            scenario: "grid_ctf",
            status: "completed",
            created_at: "2026-04-10T00:00:00Z",
          },
          {
            run_id: "run-2",
            scenario: "othello",
            status: "failed",
            created_at: "2026-04-10T01:00:00Z",
          },
        ],
        false,
      ),
    ).toBe(
      [
        "run-1  grid_ctf  completed  2026-04-10T00:00:00Z",
        "run-2  othello  failed  2026-04-10T01:00:00Z",
      ].join("\n"),
    );
  });

  it("executes list workflow with planned arguments", () => {
    const listRuns = vi.fn(() => [
      {
        run_id: "run-1",
        scenario: "grid_ctf",
        status: "completed",
        created_at: "2026-04-10T00:00:00Z",
      },
    ]);

    const output = executeListCommandWorkflow({
      plan: { limit: 10, scenario: "grid_ctf", json: false },
      listRuns,
    });

    expect(listRuns).toHaveBeenCalledWith(10, "grid_ctf");
    expect(output).toBe("run-1  grid_ctf  completed  2026-04-10T00:00:00Z");
  });
});
