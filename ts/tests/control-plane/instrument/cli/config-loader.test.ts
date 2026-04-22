/**
 * A2-I config-file auto-loader tests (Task 7.5).
 *
 * Verifies that runInstrumentCommand auto-discovers and loads
 * `.autoctx.instrument.config.{mjs,js,ts}` before running the scanner.
 *
 * Priority order: .mjs > .js > .ts (first found wins).
 */
import { describe, test, expect, beforeEach } from "vitest";
import { mkdtempSync, mkdirSync, writeFileSync, rmSync } from "node:fs";
import { tmpdir } from "node:os";
import { join, resolve, dirname } from "node:path";
import { fileURLToPath, pathToFileURL } from "node:url";
import { runInstrumentCommand } from "../../../../src/control-plane/instrument/cli/runner.js";
import {
  resetRegistryForTests,
  pluginsForLanguage,
} from "../../../../src/control-plane/instrument/registry/plugin-registry.js";

const __dirname = dirname(fileURLToPath(import.meta.url));
// Absolute path to plugin-registry source so the config file can import it.
const REGISTRY_PATH = resolve(
  __dirname,
  "../../../../src/control-plane/instrument/registry/plugin-registry.js",
);
const REGISTRY_URL = pathToFileURL(REGISTRY_PATH).href;

const scratches: string[] = [];
function scratch(): string {
  const d = mkdtempSync(join(tmpdir(), "acfg-"));
  scratches.push(d);
  return d;
}

beforeEach(() => {
  resetRegistryForTests();
  while (scratches.length > 0) {
    const d = scratches.pop()!;
    try {
      rmSync(d, { recursive: true, force: true });
    } catch {
      // ignore cleanup errors
    }
  }
});

describe("autoctx instrument config-file auto-load", () => {
  test("loads .autoctx.instrument.config.mjs if present, registers plugin", async () => {
    const cwd = scratch();
    // Write a minimal Python source file so the scanner has something to process.
    mkdirSync(join(cwd, "src"), { recursive: true });
    writeFileSync(join(cwd, "src", "app.py"), "from openai import OpenAI\n", "utf-8");

    // Config file: registers a minimal detector plugin.
    writeFileSync(
      join(cwd, ".autoctx.instrument.config.mjs"),
      `import { registerDetectorPlugin } from ${JSON.stringify(REGISTRY_URL)};
registerDetectorPlugin({
  id: "@test/cfg-loader-python",
  supports: { language: "python", sdkName: "openai" },
  treeSitterQueries: [],
  produce: () => ({ edits: [], advisories: [] }),
});
`,
      "utf-8",
    );

    const result = await runInstrumentCommand(["--output", "json"], { cwd });
    expect(result.exitCode).toBe(0);

    // The plugin was registered by the config file.
    const plugins = pluginsForLanguage("python");
    expect(plugins.some((p) => p.id === "@test/cfg-loader-python")).toBe(true);
  });

  test("loads .autoctx.instrument.config.js (lower priority than .mjs)", async () => {
    const cwd = scratch();
    mkdirSync(join(cwd, "src"), { recursive: true });
    writeFileSync(join(cwd, "src", "app.py"), "# placeholder\n", "utf-8");

    // Only .js present (no .mjs).
    writeFileSync(
      join(cwd, ".autoctx.instrument.config.js"),
      `import { registerDetectorPlugin } from ${JSON.stringify(REGISTRY_URL)};
registerDetectorPlugin({
  id: "@test/cfg-loader-js",
  supports: { language: "python", sdkName: "openai-compat" },
  treeSitterQueries: [],
  produce: () => ({ edits: [], advisories: [] }),
});
`,
      "utf-8",
    );

    const result = await runInstrumentCommand(["--output", "json"], { cwd });
    expect(result.exitCode).toBe(0);

    const plugins = pluginsForLanguage("python");
    expect(plugins.some((p) => p.id === "@test/cfg-loader-js")).toBe(true);
  });

  test(".mjs takes priority over .js when both present", async () => {
    const cwd = scratch();
    mkdirSync(join(cwd, "src"), { recursive: true });
    writeFileSync(join(cwd, "src", "app.py"), "# placeholder\n", "utf-8");

    // Both .mjs and .js present — .mjs wins.
    writeFileSync(
      join(cwd, ".autoctx.instrument.config.mjs"),
      `import { registerDetectorPlugin } from ${JSON.stringify(REGISTRY_URL)};
registerDetectorPlugin({
  id: "@test/cfg-mjs-wins",
  supports: { language: "python", sdkName: "openai-v2" },
  treeSitterQueries: [],
  produce: () => ({ edits: [], advisories: [] }),
});
`,
      "utf-8",
    );
    writeFileSync(
      join(cwd, ".autoctx.instrument.config.js"),
      `import { registerDetectorPlugin } from ${JSON.stringify(REGISTRY_URL)};
registerDetectorPlugin({
  id: "@test/cfg-js-should-not-load",
  supports: { language: "typescript", sdkName: "openai" },
  treeSitterQueries: [],
  produce: () => ({ edits: [], advisories: [] }),
});
`,
      "utf-8",
    );

    await runInstrumentCommand(["--output", "json"], { cwd });

    const pythonPlugins = pluginsForLanguage("python");
    const tsPlugins = pluginsForLanguage("typescript");
    expect(pythonPlugins.some((p) => p.id === "@test/cfg-mjs-wins")).toBe(true);
    // .js was not loaded because .mjs was found first.
    expect(tsPlugins.some((p) => p.id === "@test/cfg-js-should-not-load")).toBe(false);
  });

  test("no config file → registry stays empty (only pre-registered plugins visible)", async () => {
    const cwd = scratch();
    // No config file present.
    const result = await runInstrumentCommand(["--fail-if-empty", "--output", "json"], { cwd });
    // --fail-if-empty exits 12 when zero plugins registered.
    expect(result.exitCode).toBe(12);
  });

  test("no config file → exit 0 without --fail-if-empty", async () => {
    const cwd = scratch();
    const result = await runInstrumentCommand(["--output", "json"], { cwd });
    expect(result.exitCode).toBe(0);
  });
});
