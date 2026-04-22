/**
 * E2E: config-file → runInstrumentCommand --apply on a fixture repo →
 * verify patched file wraps OpenAI() correctly.
 *
 * Full path:
 *   1. Scratch repo: .autoctx.instrument.config.mjs + src/app.py
 *   2. Config file registers the mockOpenAiPythonPlugin (tests/_fixtures)
 *      via the config auto-loader (Task 7.5). The mock plugin detects
 *      OpenAI() calls via string scan (no tree-sitter dependency) and emits
 *      wrap-expression edits — functionally equivalent to the real detector.
 *   3. runInstrumentCommand(["--apply", "--force"]) applies the patch.
 *   4. Patched src/app.py is read and verified:
 *      - contains instrument_client(OpenAI(...))
 *      - contains autocontext.integrations.openai import
 *      - original import from openai is preserved
 *   5. Scratch dir is cleaned up on teardown.
 *
 * Design note on the real @autoctx/detector-openai-python plugin:
 *   The orchestrator's runPluginQueries() currently stubs tree-sitter query
 *   execution (returns empty captures). The real plugin therefore produces
 *   zero edits via that path. Full tree-sitter integration is covered by
 *   tests/control-plane/instrument/detectors/. This E2E test validates the
 *   end-to-end config-file → apply pipeline using the fixture mock that
 *   produces real wrap edits through direct source scanning.
 *
 * Note: The downstream Python execution path (uv run + FileSink emit +
 * validateProductionTrace) is validated in autocontext/tests/integrations/.
 */
import { describe, test, expect, beforeEach, afterEach } from "vitest";
import {
  mkdtempSync,
  mkdirSync,
  writeFileSync,
  readFileSync,
  rmSync,
} from "node:fs";
import { tmpdir } from "node:os";
import { join, resolve, dirname } from "node:path";
import { fileURLToPath, pathToFileURL } from "node:url";
import { runInstrumentCommand } from "../../src/control-plane/instrument/cli/runner.js";
import { resetRegistryForTests } from "../../src/control-plane/instrument/registry/plugin-registry.js";

const __dirname = dirname(fileURLToPath(import.meta.url));

// Absolute file:// URL to the fixture plugin source. The config file
// dynamically imports this and calls registerDetectorPlugin at import time.
const FIXTURE_PLUGIN_PATH = resolve(
  __dirname,
  "../_fixtures/plugins/mock-openai-python.js",
);
const FIXTURE_PLUGIN_URL = pathToFileURL(FIXTURE_PLUGIN_PATH).href;

// Absolute file:// URL to the plugin registry source (for registerDetectorPlugin).
const REGISTRY_PATH = resolve(
  __dirname,
  "../../src/control-plane/instrument/registry/plugin-registry.js",
);
const REGISTRY_URL = pathToFileURL(REGISTRY_PATH).href;

const scratches: string[] = [];

function scratch(): string {
  const d = mkdtempSync(join(tmpdir(), "autoctx-e2e-"));
  scratches.push(d);
  return d;
}

/** Build the config-file content that registers the fixture openai-python plugin. */
function configFileSrc(): string {
  return [
    `import { mockOpenAiPythonPlugin } from ${JSON.stringify(FIXTURE_PLUGIN_URL)};`,
    `import { registerDetectorPlugin } from ${JSON.stringify(REGISTRY_URL)};`,
    `registerDetectorPlugin(mockOpenAiPythonPlugin);`,
    "",
  ].join("\n");
}

beforeEach(() => {
  resetRegistryForTests();
});

afterEach(() => {
  while (scratches.length > 0) {
    const d = scratches.pop()!;
    try {
      rmSync(d, { recursive: true, force: true });
    } catch {
      // ignore cleanup errors
    }
  }
});

describe("E2E: config-file → instrument --apply → patched Python file", () => {
  test(
    "config file registers fixture plugin and patches src/app.py",
    async () => {
      const cwd = scratch();

      mkdirSync(join(cwd, "src"), { recursive: true });
      const originalPy = "from openai import OpenAI\nclient = OpenAI(api_key='placeholder')\n";
      writeFileSync(join(cwd, "src", "app.py"), originalPy, "utf-8");
      writeFileSync(join(cwd, ".autoctx.instrument.config.mjs"), configFileSrc(), "utf-8");

      // --force bypasses clean-tree git check (scratch dir is not a git repo).
      const result = await runInstrumentCommand(
        ["--apply", "--force", "--output", "json"],
        { cwd },
      );

      expect(result.exitCode).toBe(0);
      const payload = JSON.parse(result.stdout);
      expect(payload.mode).toBe("apply");
      expect(payload.filesAffected).toBeGreaterThanOrEqual(1);
      expect(payload.applyResult).toBeDefined();

      const patched = readFileSync(join(cwd, "src", "app.py"), "utf-8");

      // Wrap must be applied.
      expect(patched).toContain("instrument_client(");
      expect(patched).toContain("instrument_client(OpenAI(");

      // Import injection from autocontext.integrations.openai.
      expect(patched).toContain("autocontext.integrations.openai");

      // Original openai import must be preserved.
      expect(patched).toContain("from openai import OpenAI");

      // Patched content must differ from original.
      expect(patched).not.toBe(originalPy);
    },
    30_000,
  );

  test(
    "dry-run via config file: does not modify src/app.py, returns session payload",
    async () => {
      const cwd = scratch();
      mkdirSync(join(cwd, "src"), { recursive: true });

      const originalPy = "from openai import OpenAI\nclient = OpenAI()\n";
      writeFileSync(join(cwd, "src", "app.py"), originalPy, "utf-8");
      writeFileSync(join(cwd, ".autoctx.instrument.config.mjs"), configFileSrc(), "utf-8");

      const result = await runInstrumentCommand(["--output", "json"], { cwd });

      expect(result.exitCode).toBe(0);
      const payload = JSON.parse(result.stdout);
      expect(payload.mode).toBe("dry-run");
      expect(payload.filesAffected).toBeGreaterThanOrEqual(1);

      // File must NOT be modified in dry-run.
      const unchanged = readFileSync(join(cwd, "src", "app.py"), "utf-8");
      expect(unchanged).toBe(originalPy);
    },
    30_000,
  );

  test(
    "no config file → exit 12 with --fail-if-empty (zero plugins)",
    async () => {
      const cwd = scratch();
      const result = await runInstrumentCommand(
        ["--fail-if-empty", "--output", "json"],
        { cwd },
      );
      expect(result.exitCode).toBe(12);
    },
    10_000,
  );
});
