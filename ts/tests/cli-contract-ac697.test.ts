/**
 * AC-697 slice 1: TypeScript-side parity tests for docs/cli-contract.json.
 *
 * Mirror of `autocontext/tests/test_cli_contract.py`. Both sides
 * load the same JSON contract; the test runs assert that every
 * command marked `runtime_support.typescript === "yes"` is
 * actually present in `command-registry.ts` at the canonical
 * single-token path.
 *
 * Multi-token canonical paths (e.g. `["serve", "mcp"]`,
 * `["queue", "status"]`) are checked against `runtime_support`
 * being either `missing` or `intentional_gap` until AC-697
 * follow-up slices add subcommand grouping to the TypeScript
 * registry.
 */

import { describe, it, expect } from "vitest";
import { mkdtempSync, readFileSync, rmSync, writeFileSync } from "node:fs";
import { tmpdir } from "node:os";
import { join, resolve } from "node:path";
import {
  loadContract,
  PAVED_ROAD,
  resolveAlias,
  type CommandSpec,
} from "../src/cli/cli-contract.js";
import { visibleSupportedCommandNames } from "../src/cli/command-registry.js";

const CONTRACT_PATH = resolve(import.meta.dirname, "..", "..", "docs", "cli-contract.json");

describe("AC-697 CLI contract — schema sanity", () => {
  it("file exists and parses", () => {
    const contract = loadContract(CONTRACT_PATH);
    expect(contract.schema_version).toBe(2);
    expect(contract.commands.length).toBeGreaterThan(0);
  });

  it("fully specifies export as the first v2 conformance command", () => {
    const contract = loadContract(CONTRACT_PATH);
    const command = contract.commands.find((candidate) => candidate.id === "export");
    expect(command?.positionals[0]?.name).toBe("run-id");
    const format = command?.flags.find((flag) => flag.name === "format");
    expect(format?.default).toBe("json");
    expect(format?.choices).toEqual(["json", "pi-package"]);
    expect(format?.value_aliases).toEqual({ strategy: "json" });
    expect(command?.output).toEqual({
      modes: ["json", "text"],
      streaming: "single",
      success_stream: "stdout",
      error_stream: "stderr",
      schemas: {
        artifact: "cli-schemas/strategy-package-v1.schema.json",
        receipt: "cli-schemas/export-receipt-v1.schema.json",
        error: "cli-schemas/cli-error-v1.schema.json",
      },
    });
    for (const schemaPath of Object.values(command?.output?.schemas ?? {})) {
      expect(readFileSync(resolve(CONTRACT_PATH, "..", schemaPath), "utf-8")).toBeTruthy();
    }
    expect(command?.exit_codes).toEqual({ success: 0, usage: 2, execution: 1 });
    expect(command?.examples.length).toBeGreaterThan(0);
  });

  it("keeps version-1 command entries loadable with compatibility defaults", () => {
    const raw = JSON.parse(readFileSync(CONTRACT_PATH, "utf-8")) as {
      schema_version: number;
      commands: Array<Record<string, unknown>>;
    };
    raw.schema_version = 1;
    const command = raw.commands.find((candidate) => candidate.id === "export");
    expect(command).toBeDefined();
    for (const field of ["positionals", "output", "exit_codes", "examples"]) {
      delete command?.[field];
    }
    if (Array.isArray(command?.flags)) {
      for (const flag of command.flags) {
        if (flag && typeof flag === "object" && !Array.isArray(flag)) {
          for (const field of ["short_names", "default", "choices", "value_aliases"]) {
            delete (flag as Record<string, unknown>)[field];
          }
        }
      }
    }
    const directory = mkdtempSync(join(tmpdir(), "autoctx-cli-contract-v1-"));
    const path = join(directory, "cli-contract.json");
    try {
      writeFileSync(path, JSON.stringify(raw), "utf-8");
      const loaded = loadContract(path);
      const exportCommand = loaded.commands.find((candidate) => candidate.id === "export");
      expect(loaded.schema_version).toBe(1);
      expect(exportCommand?.positionals).toEqual([]);
      expect(exportCommand?.output?.success_stream).toBe("stdout");
      expect(exportCommand?.exit_codes).toEqual({ success: 0, usage: 2, execution: 1 });
    } finally {
      rmSync(directory, { recursive: true, force: true });
    }
  });

  it("contract covers every paved-road command id", () => {
    const contract = loadContract(CONTRACT_PATH);
    const ids = new Set(contract.commands.map((c) => c.id));
    for (const required of PAVED_ROAD) {
      expect(ids.has(required)).toBe(true);
    }
  });

  it("no duplicate command ids", () => {
    const contract = loadContract(CONTRACT_PATH);
    const ids = contract.commands.map((c) => c.id);
    expect(new Set(ids).size).toBe(ids.length);
  });

  it("alias paths are unique across commands", () => {
    const contract = loadContract(CONTRACT_PATH);
    const seen = new Map<string, string>();
    for (const cmd of contract.commands) {
      for (const alias of cmd.aliases) {
        expect(
          seen.has(alias),
          `alias ${JSON.stringify(alias)} listed under both ${seen.get(alias)} and ${cmd.id}`,
        ).toBe(false);
        seen.set(alias, cmd.id);
      }
    }
  });

  it("intentional_gap entries carry a non-empty reason", () => {
    const contract = loadContract(CONTRACT_PATH);
    for (const cmd of contract.commands) {
      for (const runtime of ["python", "typescript"] as const) {
        const support = cmd.runtime_support[runtime];
        if (support.status === "intentional_gap") {
          expect(
            support.reason && support.reason.length > 0,
            `${cmd.id}.${runtime} marked intentional_gap without reason`,
          ).toBe(true);
        }
      }
    }
  });

  it("paved-road constant matches audience filter", () => {
    const contract = loadContract(CONTRACT_PATH);
    const fromTag = new Set(
      contract.commands.filter((c) => c.audience === "paved_road").map((c) => c.id),
    );
    expect(new Set(PAVED_ROAD)).toEqual(fromTag);
  });
});

describe("AC-697 CLI contract — TypeScript parity", () => {
  it("every yes-supported command is registered in command-registry", () => {
    const contract = loadContract(CONTRACT_PATH);
    const registered = new Set(visibleSupportedCommandNames());
    for (const cmd of contract.commands) {
      if (cmd.runtime_support.typescript.status === "yes") {
        if (cmd.path.length === 1) {
          expect(
            registered.has(cmd.path[0]),
            `contract claims TS support for ${cmd.id} at ${JSON.stringify(cmd.path)} but registry has no matching command`,
          ).toBe(true);
        } else if (cmd.path.length >= 2) {
          // AC-697 slice 4: multi-token canonical paths must at least
          // have their parent token registered. Subcommand-level
          // dispatch (e.g. `cmdScenario` checking for `create`) is
          // internal to each handler, so this partial check catches
          // the case where the parent command isn't even mounted.
          // Strengthening to fully verify the subcommand path lives
          // in a future slice that introduces a TS subcommand
          // registry.
          expect(
            registered.has(cmd.path[0]),
            `contract claims TS support for ${cmd.id} at ${JSON.stringify(cmd.path)} but the parent token ${JSON.stringify(cmd.path[0])} is not a registered command`,
          ).toBe(true);
        }
      }
    }
  });
});

describe("AC-697 CLI contract — friction-point invariants", () => {
  it("status canonical meaning is run status", () => {
    const contract = loadContract(CONTRACT_PATH);
    const status = contract.commands.find((c: CommandSpec) => c.id === "status");
    expect(status).toBeDefined();
    expect(status?.domain_concept).toBe("Run");
    expect(status?.summary.toLowerCase()).toContain("run");
    expect(status?.positionals).toEqual([
      expect.objectContaining({ name: "run-id", type: "string", required: false }),
    ]);
    expect(status?.flags.map((flag) => flag.name)).toEqual(["run-id", "json"]);
    expect(status?.output?.error_stream).toBe("stderr");
    expect(status?.exit_codes).toEqual({ success: 0, usage: 2, execution: 1 });
    expect(status?.examples).not.toContain("autoctx status");
  });

  it("solve is not a domain noun", () => {
    const contract = loadContract(CONTRACT_PATH);
    const solve = contract.commands.find((c: CommandSpec) => c.id === "solve");
    expect(solve).toBeDefined();
    expect(solve?.domain_concept).not.toBe("Mission");
    expect(solve?.domain_concept).not.toBe("Scenario");
  });

  it("iterations is the canonical iteration-control flag (no advertised aliases in slice 1)", () => {
    // PR #981 review (P2): aliases were dropped until AC-697
    // follow-up slices ship the alias plumbing in both runtimes.
    // The contract documents only honored options today.
    const contract = loadContract(CONTRACT_PATH);
    const solve = contract.commands.find((c: CommandSpec) => c.id === "solve");
    const iterFlag = solve?.flags.find((f) => f.name === "iterations");
    expect(iterFlag).toBeDefined();
    expect(iterFlag?.aliases).toEqual([]);
  });

  it("queue status does not occupy top-level status semantic", () => {
    const contract = loadContract(CONTRACT_PATH);
    const status = contract.commands.find((c: CommandSpec) => c.id === "status");
    expect(status?.domain_concept).toBe("Run");
    const queueStatus = contract.commands.find((c: CommandSpec) => c.id === "queue.status");
    if (queueStatus !== undefined) {
      expect(queueStatus.path).not.toEqual(["status"]);
    }
  });
});

describe("AC-697 CLI contract — alias resolution helper", () => {
  it("resolves a known alias to its canonical id", () => {
    const contract = loadContract(CONTRACT_PATH);
    expect(resolveAlias(contract, "new-scenario")).toBe("scenario.create");
    expect(resolveAlias(contract, "mcp-serve")).toBe("serve.mcp");
  });

  it("returns undefined for an unknown alias", () => {
    const contract = loadContract(CONTRACT_PATH);
    expect(resolveAlias(contract, "this-is-not-an-alias")).toBeUndefined();
  });
});
