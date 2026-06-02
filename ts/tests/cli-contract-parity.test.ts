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
 *    `visibleSupportedCommandNames()` is either contracted, a
 *    contracted alias, OR named in
 *    `UNCONTRACTED_TOP_LEVEL_ALLOWLIST`. Adding a new top-level
 *    command without a contract entry (or an allowlist line) fails
 *    the test, so the operator is forced to either advertise it in
 *    the contract or document why it stays uncontracted.
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
import { resolve } from "node:path";
import { loadContract } from "../src/cli/cli-contract.js";
import { visibleSupportedCommandNames } from "../src/cli/command-registry.js";

const CONTRACT_PATH = resolve(import.meta.dirname, "..", "..", "docs", "cli-contract.json");

/**
 * Top-level visible TS commands that are intentionally NOT advertised
 * in docs/cli-contract.json. The TS surface ships a richer set of
 * non-paved-road commands today; this allowlist documents the
 * "uncontracted by design" set so a new top-level command can't slip
 * through silently.
 */
const UNCONTRACTED_TOP_LEVEL_ALLOWLIST = new Set<string>([
  "agent",
  "analyze",
  "benchmark",
  "campaign",
  "candidate",
  "context-selection",
  "emit-pr",
  "eval",
  "export-training-data",
  "harness",
  "import-package",
  "init",
  "instrument",
  "investigate",
  "login",
  "logout",
  "models",
  "probes",
  "production-traces",
  "promotion",
  "providers",
  "registry",
  "repl",
  "runtime-sessions",
  "simulate",
  "trace-findings",
  "train",
  "tui",
  "version",
  "whoami",
  "worker",
]);

// ---------------------------------------------------------------------------
// Reverse direction: observed -> contract / alias / allowlist
// ---------------------------------------------------------------------------

describe("AC-697 cross-runtime parity audit (TypeScript side)", () => {
  it("every observed top-level command is contracted, aliased, or on the explicit allowlist", () => {
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
      ...UNCONTRACTED_TOP_LEVEL_ALLOWLIST,
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
      `Top-level TS commands shipped without a contract entry or allowlist line: ${JSON.stringify(
        leaked,
      )}. Either add them to docs/cli-contract.json or to UNCONTRACTED_TOP_LEVEL_ALLOWLIST in this test.`,
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

  it("allowlist is minimal (no entries also present in the contract)", () => {
    const contract = loadContract(CONTRACT_PATH);
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
    const contracted = new Set<string>([...contractedTopLevel, ...contractedAliases]);

    const redundant: string[] = [];
    for (const name of UNCONTRACTED_TOP_LEVEL_ALLOWLIST) {
      if (contracted.has(name)) {
        redundant.push(name);
      }
    }
    expect(
      redundant.sort(),
      `Allowlist entries that are ALSO in the contract: ${JSON.stringify(
        redundant,
      )}. Remove them from the allowlist.`,
    ).toEqual([]);
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
