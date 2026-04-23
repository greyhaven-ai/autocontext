import { describe, expect, it } from "vitest";

import {
  buildCliHelp,
  resolveCliCommand,
  visibleCommandNames,
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

  it("classifies commands by dispatch surface", () => {
    expect(resolveCliCommand("run")).toEqual({ kind: "db", command: "run" });
    expect(resolveCliCommand("mission")).toEqual({
      kind: "db",
      command: "mission",
    });
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
