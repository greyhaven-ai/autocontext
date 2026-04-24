import { describe, expect, it } from "vitest";

import {
  buildCliHelp,
  resolveCliCommand,
  visibleCommandNames,
  visibleSupportedCommandNames,
} from "../src/cli/command-registry.js";

describe("CLI command registry", () => {
  it("keeps visible command metadata unique and present in help", () => {
    const names = visibleCommandNames();
    const help = buildCliHelp();

    expect(new Set(names).size).toBe(names.length);
    for (const name of names) {
      expect(help).toContain(name);
    }
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
