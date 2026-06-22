import { mkdtempSync, readdirSync, readFileSync, rmSync } from "node:fs";
import { tmpdir } from "node:os";
import { join } from "node:path";
import { afterEach, describe, expect, it } from "vitest";

type ImportContractModule = {
  import_time_filesystem_writes: boolean;
  module: string;
  runtime: "python" | "typescript";
  runtime_setup: string;
};

const CONTRACT = JSON.parse(
  readFileSync(
    join(import.meta.dirname, "..", "..", "docs", "strategy-package-import-contract.json"),
    "utf-8",
  ),
) as { modules: ImportContractModule[] };

const TYPESCRIPT_IMPORTS: Record<string, () => Promise<unknown>> = {
  "src/knowledge/index.js": () => import("../src/knowledge/index.js"),
  "src/knowledge/package.js": () => import("../src/knowledge/package.js"),
};

function snapshotDirectory(dir: string): string[] {
  return readdirSync(dir, { recursive: true })
    .map((entry) => String(entry))
    .sort();
}

describe("strategy package import contract", () => {
  let dirs: string[] = [];

  afterEach(() => {
    for (const dir of dirs) {
      rmSync(dir, { recursive: true, force: true });
    }
    dirs = [];
  });

  it("declares TypeScript strategy-package imports as filesystem-pure", () => {
    const modules = CONTRACT.modules.filter((entry) => entry.runtime === "typescript");
    expect(modules).not.toHaveLength(0);
    for (const entry of modules) {
      expect(entry.import_time_filesystem_writes, entry.module).toBe(false);
      expect(entry.runtime_setup, entry.module).toContain("Call");
    }
  });

  it("imports strategy-package modules without creating runtime files", async () => {
    for (const entry of CONTRACT.modules.filter((item) => item.runtime === "typescript")) {
      const importModule = TYPESCRIPT_IMPORTS[entry.module];
      expect(importModule, entry.module).toBeDefined();

      const dir = mkdtempSync(join(tmpdir(), "ac-package-import-"));
      dirs.push(dir);
      const previousCwd = process.cwd();
      try {
        process.chdir(dir);
        const before = snapshotDirectory(dir);
        await importModule();
        expect(snapshotDirectory(dir), entry.module).toEqual(before);
      } finally {
        process.chdir(previousCwd);
      }
    }
  });
});
