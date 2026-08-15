import { spawnSync } from "node:child_process";
import { createRequire } from "node:module";
import { existsSync, readFileSync } from "node:fs";
import { join, resolve } from "node:path";
import { beforeAll, describe, expect, test } from "vitest";

const ROOT = resolve(import.meta.dirname, "..", "..");
const PACKAGE_JSON = join(ROOT, "package.json");
const pkg = JSON.parse(readFileSync(PACKAGE_JSON, "utf8")) as {
  exports: Record<string, Record<string, string> | string>;
  scripts: Record<string, string>;
};

const CJS_SUBPATHS = [
  {
    subpath: "./integrations/_shared",
    target: "./dist/cjs/integrations/_shared/index.cjs",
    exportName: "FileSink",
  },
  {
    subpath: "./integrations/anthropic",
    target: "./dist/cjs/integrations/anthropic/index.cjs",
    exportName: "instrumentClient",
  },
  {
    subpath: "./integrations/openai",
    target: "./dist/cjs/integrations/openai/index.cjs",
    exportName: "instrumentClient",
  },
  {
    subpath: "./detectors/openai-python",
    target: "./dist/cjs/control-plane/instrument/detectors/openai-python/index.cjs",
    exportName: "plugin",
  },
  {
    subpath: "./detectors/openai-ts",
    target: "./dist/cjs/control-plane/instrument/detectors/openai-ts/index.cjs",
    exportName: "plugin",
  },
  {
    subpath: "./detectors/anthropic-python",
    target: "./dist/cjs/control-plane/instrument/detectors/anthropic-python/index.cjs",
    exportName: "plugin",
  },
  {
    subpath: "./detectors/anthropic-ts",
    target: "./dist/cjs/control-plane/instrument/detectors/anthropic-ts/index.cjs",
    exportName: "plugin",
  },
] as const;

describe("CommonJS integration and detector subpath exports", () => {
  beforeAll(() => {
    if (CJS_SUBPATHS.every(({ target }) => existsSync(join(ROOT, target)))) return;

    const result = spawnSync("node", ["scripts/build-public-subpaths-cjs.mjs"], {
      cwd: ROOT,
      encoding: "utf8",
    });
    if (result.status !== 0) {
      throw new Error(`CJS subpath build failed:\n${result.stdout}\n${result.stderr}`);
    }
  }, 120_000);

  test("the package build invokes the focused CJS build", () => {
    expect(pkg.scripts.build).toContain("npm run build:public-subpaths-cjs");
  });

  test.each(CJS_SUBPATHS)("$subpath advertises an existing require target", ({ subpath, target }) => {
    const entry = pkg.exports[subpath] as Record<string, string>;
    expect(entry.require).toBe(target);
    expect(existsSync(join(ROOT, target))).toBe(true);
  });

  test.each(CJS_SUBPATHS)("$subpath can be required through the exports map", ({ subpath, exportName }) => {
    const requireFromPackage = createRequire(PACKAGE_JSON);
    const publicName = `autoctx/${subpath.slice(2)}`;
    const loaded = requireFromPackage(publicName) as Record<string, unknown>;
    expect(loaded[exportName]).toBeDefined();
  });
});
