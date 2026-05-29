/**
 * AC-697 slice 8: TS `autoctx queue add` canonical subcommand.
 *
 * Slice 1 (PR #981) pinned the contract: TS exposed only the legacy
 * `autoctx queue -s <spec> ...` form for queue-add, so `queue` was
 * marked `intentional_gap` for TS with the reason "AC-697 follow-up
 * slice adds `autoctx queue add ...` and moves queue-add to the
 * canonical path." Slice 2 (PR #997) added the `queue status`
 * subcommand. This slice closes the last contract gap (other than the
 * explicitly out-of-scope `mission` Python entry) by promoting
 * `cmdQueue` to dispatch an explicit `add` subcommand while
 * preserving the legacy `-s <spec>` form for backward compatibility.
 *
 * The CLI dispatch itself is exercised end-to-end by `cli/index.ts`;
 * the planQueueCommand / renderQueuedTaskResult workflow it calls
 * already has unit coverage. The tests below pin the slice-8
 * contract flip and the help-text update so the canonical surface
 * stays discoverable from `autoctx queue --help`.
 */

import { readFileSync } from "node:fs";
import { resolve } from "node:path";

import { describe, expect, test } from "vitest";

import {
  planQueueCommand,
  QUEUE_HELP_TEXT,
  renderQueuedTaskResult,
} from "../src/cli/queue-status-command-workflow.js";

describe("AC-697 slice 8: contract entry for TS `queue` flips to yes", () => {
  test("docs/cli-contract.json: TS `queue` is yes (canonical `queue add` subcommand)", () => {
    const path = resolve(import.meta.dirname, "..", "..", "docs", "cli-contract.json");
    const contract = JSON.parse(readFileSync(path, "utf-8")) as {
      commands: {
        id: string;
        runtime_support: {
          python: { status: string; reason?: string };
          typescript: { status: string; reason?: string };
        };
      }[];
    };
    const byId = new Map(contract.commands.map((c) => [c.id, c]));
    const queue = byId.get("queue");
    expect(queue).toBeDefined();
    expect(queue!.runtime_support.typescript.status).toBe("yes");
    expect(queue!.runtime_support.typescript.reason).toBeUndefined();
    expect(queue!.runtime_support.python.status).toBe("yes");
  });
});

describe("AC-697 slice 8: QUEUE_HELP_TEXT advertises canonical `add` subcommand", () => {
  test("help text mentions `queue add` as the canonical form", () => {
    expect(QUEUE_HELP_TEXT).toContain("autoctx queue add");
  });

  test("help text still documents the legacy `-s <spec>` form for backward compat", () => {
    // The slice-1 contract's `intentional_gap` reason explicitly
    // preserved the legacy form ("TypeScript currently exposes
    // `autoctx queue -s <spec> ...`"); slice 8 keeps it as an alias.
    expect(QUEUE_HELP_TEXT).toContain("-s");
  });
});

describe("AC-697 slice 8: planQueueCommand stays the workhorse for both forms", () => {
  test("planQueueCommand plans the same request regardless of which surface fed it", () => {
    // The slice-8 dispatch changes how `subArgs` are routed in
    // `cmdQueue` (intercepting `add` before the legacy parseArgs);
    // the actual planning workhorse is unchanged. Pin that contract.
    const plan = planQueueCommand(
      {
        spec: "demo-scenario",
        prompt: "do a thing",
        rubric: "be helpful",
        priority: "3",
      },
      null,
    );
    expect(plan.specName).toBe("demo-scenario");
    expect(plan.request.taskPrompt).toBe("do a thing");
    expect(plan.request.rubric).toBe("be helpful");
    expect(plan.request.priority).toBe(3);
  });

  test("renderQueuedTaskResult JSON shape is preserved", () => {
    const rendered = renderQueuedTaskResult({ taskId: "task-42", specName: "demo" });
    const parsed = JSON.parse(rendered) as {
      taskId: string;
      specName: string;
      status: string;
    };
    expect(parsed).toEqual({ taskId: "task-42", specName: "demo", status: "queued" });
  });
});
