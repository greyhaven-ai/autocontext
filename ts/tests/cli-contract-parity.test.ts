/**
 * Cross-runtime CLI parity audit (TypeScript side).
 *
 * The forward direction (contract -> command-registry registration) is
 * covered by `cli-contract-ac697.test.ts`. The audit below adds the
 * REVERSE direction plus cross-runtime invariants so accidental drift
 * surfaces immediately.
 *
 * What this pins:
 *
 * 1. **Reverse direction**: every visible command in
 *    `visibleSupportedCommandNames()` is contracted or is a
 *    contracted compatibility alias. Public commands cannot escape
 *    the contract through an allowlist.
 *
 * 2. **Alias registration**: every contracted alias must still
 *    resolve to a registered TS command name. Pins that the legacy
 *    invocations (`autoctx mcp-serve`, `autoctx new-scenario`) still
 *    work after future refactors.
 *
 * 3. **Cross-runtime invariants**: command ids are unique, well-
 *    formed, and runtime-agnostic (no `python.X` / `ts.X` prefix).
 *    The contract is a single source of truth across runtimes; these
 *    assertions trap a hand-edit that introduces a runtime-specific
 *    id.
 */

import { describe, it, expect } from "vitest";
import { execFileSync } from "node:child_process";
import { resolve } from "node:path";
import { loadContract } from "../src/cli/cli-contract.js";
import { visibleSupportedCommandNames } from "../src/cli/command-registry.js";

const CONTRACT_PATH = resolve(import.meta.dirname, "..", "..", "docs", "cli-contract.json");
const CLI_PATH = resolve(import.meta.dirname, "..", "src", "cli", "index.ts");
const TSX_PATH = resolve(import.meta.dirname, "..", "node_modules", ".bin", "tsx");
const LIVE_HELP = new Map<string, string>();
const IMPLEMENTATION_HISTORY = /\bAC-\d+\b|\bPR\s*#?\d+\b|\bslice(?:s|[- ]\d+[a-z]?)?\b|\binternal[- ]layer\b/i;

function loadLiveHelp(path: readonly string[]): string {
  const key = path.join("\0");
  const cached = LIVE_HELP.get(key);
  if (cached !== undefined) return cached;
  const help = execFileSync(
    TSX_PATH,
    [CLI_PATH, ...path, "--help"],
    {
      encoding: "utf8",
      env: { ...process.env, NODE_NO_WARNINGS: "1" },
      timeout: 10_000,
    },
  );
  LIVE_HELP.set(key, help);
  return help;
}

// ---------------------------------------------------------------------------
// Reverse direction: observed -> contract / alias
// ---------------------------------------------------------------------------

describe("AC-697 cross-runtime parity audit (TypeScript side)", () => {
  it("every observed top-level command is contracted or aliased", () => {
    const contract = loadContract(CONTRACT_PATH);
    const observed = new Set(visibleSupportedCommandNames());

    const contractedTopLevel = new Set<string>();
    for (const cmd of contract.commands) {
      if (cmd.path.length >= 1) {
        contractedTopLevel.add(cmd.path[0]);
      }
    }
    const contractedAliases = new Set<string>();
    for (const cmd of contract.commands) {
      for (const alias of cmd.aliases) {
        contractedAliases.add(alias);
      }
    }
    const accountedFor = new Set<string>([
      ...contractedTopLevel,
      ...contractedAliases,
    ]);

    const leaked: string[] = [];
    for (const name of observed) {
      if (!accountedFor.has(name)) {
        leaked.push(name);
      }
    }
    leaked.sort();
    expect(
      leaked,
      `Top-level TS commands shipped without a contract entry: ${JSON.stringify(
        leaked,
      )}. Add them to docs/cli-contract.json or hide them from public help.`,
    ).toEqual([]);
  });

  it("every contracted alias resolves to a registered TS command name", () => {
    const contract = loadContract(CONTRACT_PATH);
    const observed = new Set(visibleSupportedCommandNames());
    for (const cmd of contract.commands) {
      for (const alias of cmd.aliases) {
        expect(
          observed.has(alias),
          `contracted alias ${JSON.stringify(alias)} on ${JSON.stringify(
            cmd.id,
          )} is no longer a registered TS command`,
        ).toBe(true);
      }
    }
  });

  it(
    "every contracted npm flag is discoverable from live command help",
    () => {
      const contract = loadContract(CONTRACT_PATH);
      for (const command of contract.commands) {
        if (command.runtime_support.typescript.status !== "yes") continue;
        const shape = command.runtime_shapes.typescript;
        if (!shape || shape.flags.length === 0) continue;
        const help = loadLiveHelp(command.path);
        for (const flag of shape.flags) {
          for (const longName of [flag.name, ...flag.aliases]) {
            expect(
              help.includes(`--${longName}`),
              `${command.id} help omits --${longName}`,
            ).toBe(true);
          }
        }
      }
    },
    90_000,
  );

  it(
    "keeps implementation history out of every live npm help surface",
    () => {
      const contract = loadContract(CONTRACT_PATH);
      for (const command of contract.commands) {
        if (command.runtime_support.typescript.status !== "yes") continue;
        const help = loadLiveHelp(command.path);
        expect(
          IMPLEMENTATION_HISTORY.test(help),
          `${command.id} help exposes implementation history`,
        ).toBe(false);
      }
    },
    90_000,
  );

  it("keeps implementation history out of contract summaries", () => {
    const contract = loadContract(CONTRACT_PATH);
    for (const command of contract.commands) {
      expect(
        IMPLEMENTATION_HISTORY.test(command.summary),
        `${command.id} summary exposes implementation history`,
      ).toBe(false);
    }
  });

});

// ---------------------------------------------------------------------------
// Cross-runtime invariants
// ---------------------------------------------------------------------------

describe("AC-697 cross-runtime parity audit — id invariants", () => {
  it("command ids are unique and well-formed", () => {
    const contract = loadContract(CONTRACT_PATH);
    const seen = new Set<string>();
    for (const cmd of contract.commands) {
      expect(cmd.id, "empty command id").toBeTruthy();
      expect(seen.has(cmd.id), `duplicate command id ${JSON.stringify(cmd.id)}`).toBe(false);
      seen.add(cmd.id);
      for (const ch of cmd.id) {
        const isAllowed = /[a-z0-9._-]/i.test(ch);
        expect(
          isAllowed,
          `command id ${JSON.stringify(cmd.id)} contains illegal character ${JSON.stringify(ch)}`,
        ).toBe(true);
      }
    }
  });

  it("no command id uses a runtime-specific prefix", () => {
    const contract = loadContract(CONTRACT_PATH);
    const forbidden = ["python.", "py.", "typescript.", "ts."];
    for (const cmd of contract.commands) {
      for (const prefix of forbidden) {
        expect(
          cmd.id.startsWith(prefix),
          `command id ${JSON.stringify(cmd.id)} uses a runtime-specific prefix; the contract is single-sourced across runtimes`,
        ).toBe(false);
      }
    }
  });

  it("no per-runtime path divergence on commands claimed yes by both", () => {
    const contract = loadContract(CONTRACT_PATH);
    for (const cmd of contract.commands) {
      if (
        cmd.runtime_support.python.status === "yes" &&
        cmd.runtime_support.typescript.status === "yes"
      ) {
        // No per-runtime path override is allowed by the schema; the
        // existence of `cmd.path` as a single field is what guarantees
        // parity. Surface the invariant explicitly so a future schema
        // change that introduces per-runtime paths trips this assert.
        expect(cmd.path.length, `command ${JSON.stringify(cmd.id)} has empty path`).toBeGreaterThan(
          0,
        );
      }
    }
  });
});
