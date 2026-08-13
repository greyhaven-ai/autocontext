import { describe, expect, it } from "vitest";
import { readFileSync } from "node:fs";
import { resolve } from "node:path";

import {
  buildCliHelp,
  resolveCliCommand,
  visibleSupportedCommandNames,
} from "../src/cli/command-registry.js";

const HELP_LAYOUT = JSON.parse(
  readFileSync(resolve(import.meta.dirname, "..", "..", "docs", "cli-fixtures", "help-layout-v1.json"), "utf-8"),
) as { categories: string[]; paved_road: string[] };

describe("CLI command registry", () => {
  it("keeps visible command metadata unique and present in help", () => {
    const names = visibleSupportedCommandNames();
    const help = buildCliHelp({ all: true });

    expect(new Set(names).size).toBe(names.length);
    for (const name of names) {
      expect(help).toContain(name);
    }
  });

  it("keeps the six paved-road commands first in workflow order", () => {
    const help = buildCliHelp();
    const offsets = HELP_LAYOUT.paved_road.map((name) =>
      help.indexOf(`  ${name}`),
    );

    expect(offsets.every((offset) => offset >= 0)).toBe(true);
    expect(offsets).toEqual([...offsets].sort((left, right) => left - right));
    for (const category of HELP_LAYOUT.categories) {
      expect(help).toContain(`${category}:`);
    }
    expect(help).not.toContain("new-scenario");
    expect(help).not.toContain("mcp-serve");
    expect(help).not.toContain("Python-only");
    expect(help).toContain("--help --all");
  });

  it("describes top-level status as run status", () => {
    const help = buildCliHelp();

    expect(help).toMatch(/status\s+Show run status/);
    expect(help).not.toMatch(/status\s+Show queue status/);
  });

  it("keeps advanced commands and compatibility aliases in expanded help", () => {
    const help = buildCliHelp({ all: true });

    expect(help).toContain("Advanced:");
    expect(help).toContain("benchmark");
    expect(help).toContain("Compatibility aliases (deprecated):");
    expect(help).toContain("new-scenario");
    expect(help).toContain("mcp-serve");
    expect(help).not.toContain("Python-only");
  });

  it("exposes supported commands separately from Python-only help entries", () => {
    const names = visibleSupportedCommandNames();

    expect(names).toEqual(
      expect.arrayContaining([
        "train",
        "simulate",
        "investigate",
        "analyze",
        "candidate",
        "eval",
        "promotion",
        "registry",
        "emit-pr",
        "production-traces",
        "instrument",
      ]),
    );
    expect(names).not.toContain("ecosystem");
  });

  it("classifies commands by dispatch surface", () => {
    expect(resolveCliCommand("run")).toEqual({ kind: "db", command: "run" });
    expect(resolveCliCommand("runtime-sessions")).toEqual({
      kind: "db",
      command: "runtime-sessions",
    });
    expect(resolveCliCommand("context-selection")).toEqual({
      kind: "no-db",
      command: "context-selection",
    });
    expect(resolveCliCommand("mission")).toEqual({
      kind: "db",
      command: "mission",
    });
    expect(resolveCliCommand("solve")).toEqual({ kind: "db", command: "solve" });
    expect(resolveCliCommand("init")).toEqual({ kind: "no-db", command: "init" });
    expect(resolveCliCommand("registry")).toEqual({
      kind: "control-plane",
      command: "registry",
    });
    expect(resolveCliCommand("ecosystem")).toEqual({
      kind: "python-only",
      command: "ecosystem",
    });
    expect(resolveCliCommand("definitely-not-real")).toEqual({
      kind: "unknown",
      command: "definitely-not-real",
    });
  });
});
