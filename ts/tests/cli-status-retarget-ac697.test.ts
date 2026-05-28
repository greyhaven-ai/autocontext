/**
 * AC-697 slice 2: TS status retargeting contract + workflow tests.
 *
 * Slice 1 (PR #981) pinned the canonical contract: top-level
 * `autoctx status` means run-status; queue-pending count lives under
 * `autoctx queue status`. This slice flips the contract entries from
 * `intentional_gap` to `yes` for TS and retargets the actual CLI
 * dispatch in `cli/index.ts`:
 *
 *   - `cmdStatus` errors out when --run-id is missing (no fallthrough
 *     to queue-pending).
 *   - `cmdQueue` inspects its first arg for "status" and routes to
 *     `executeStatusCommandWorkflow` / `renderStatusResult` (the same
 *     workflow that used to live behind top-level `status`).
 *
 * The CLI dispatch itself is exercised end-to-end by `cli/index.ts`;
 * the workflow it calls already has unit-test coverage in
 * `queue-status-command-workflow.test.ts`. The new tests below pin
 * the slice-2 contract state and assert the workflow shape stays
 * stable across the move (so the JSON payload `cmdQueue status`
 * emits matches what the slice-1 contract pins).
 */

import { readFileSync } from "node:fs";
import { resolve } from "node:path";

import { describe, expect, test } from "vitest";

import {
  executeStatusCommandWorkflow,
  renderStatusResult,
} from "../src/cli/queue-status-command-workflow.js";

describe("AC-697 slice 2: contract entries flipped to yes for TS status + queue.status", () => {
  test("docs/cli-contract.json: TS `status` is yes (retargeted to run-status)", () => {
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
    const status = byId.get("status");
    expect(status).toBeDefined();
    expect(status!.runtime_support.typescript.status).toBe("yes");
    expect(status!.runtime_support.python.status).toBe("yes");
  });

  test("docs/cli-contract.json: TS `queue.status` is yes (new subcommand)", () => {
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
    const queueStatus = byId.get("queue.status");
    expect(queueStatus).toBeDefined();
    expect(queueStatus!.runtime_support.typescript.status).toBe("yes");
  });

  test("docs/cli-contract.json: Python `queue.status` retains an intentional_gap reason naming action-positional dispatch", () => {
    // Python's `autoctx queue status` works via the action-positional
    // dispatch in the existing queue typer command (the slice-2
    // Python code added `action="status"` handling), but the contract
    // walker reads Typer's registered subcommands and will not see
    // `status` as a registered child of `queue` until a follow-up
    // slice promotes `queue` to a sub-Typer group. The reason field
    // documents this intentional gap so reviewers can tell apart
    // "decided not to ship" from "shipped via different mechanism".
    const path = resolve(import.meta.dirname, "..", "..", "docs", "cli-contract.json");
    const contract = JSON.parse(readFileSync(path, "utf-8")) as {
      commands: {
        id: string;
        runtime_support: {
          python: { status: string; reason?: string };
        };
      }[];
    };
    const byId = new Map(contract.commands.map((c) => [c.id, c]));
    const queueStatus = byId.get("queue.status");
    expect(queueStatus).toBeDefined();
    expect(queueStatus!.runtime_support.python.status).toBe("intentional_gap");
    expect(queueStatus!.runtime_support.python.reason ?? "").toContain("action-positional");
  });

  test("`status` summary continues to pin run-status as the canonical meaning", () => {
    const path = resolve(import.meta.dirname, "..", "..", "docs", "cli-contract.json");
    const contract = JSON.parse(readFileSync(path, "utf-8")) as {
      commands: { id: string; summary: string; domain_concept: string | null }[];
    };
    const status = contract.commands.find((c) => c.id === "status");
    expect(status).toBeDefined();
    expect(status!.domain_concept).toBe("Run");
    expect(status!.summary.toLowerCase()).toContain("run");
  });
});

describe("AC-697 slice 2: queue-pending workflow shape is preserved across the move", () => {
  test("executeStatusCommandWorkflow returns { pendingCount } and renderStatusResult serializes it", () => {
    // The workflow that used to drive top-level `status` is the same
    // one cmdQueue now invokes when given the `status` subcommand;
    // pin the contract that downstream JSON consumers (the slice-2
    // contract's `output_contract: "json"`) depend on.
    const result = executeStatusCommandWorkflow({
      store: {
        migrate: () => undefined,
        pendingTaskCount: () => 7,
        close: () => undefined,
      },
      migrationsDir: "/tmp/unused",
    });
    expect(result).toEqual({ pendingCount: 7 });
    const json = renderStatusResult(result);
    const parsed = JSON.parse(json) as { pendingCount: number };
    expect(parsed.pendingCount).toBe(7);
  });
});
