/**
 * E2E: real @autoctx/detector-openai-python and @autoctx/detector-openai-ts
 * plugins end-to-end through `runInstrumentCommand --apply`.
 *
 * Mirrors `openai-end-to-end.test.ts` but uses the real detector plugins
 * instead of the fixture mock. Real plugins depend on tree-sitter query
 * execution (Fixes 1-3 in orchestrator).
 *
 * Full path (Python):
 *   1. Scratch repo: `src/app.py` with `from openai import OpenAI; client = OpenAI()`
 *   2. Config file registers @autoctx/detector-openai-python (the real plugin).
 *   3. `runInstrumentCommand(["--apply", "--force"])` applies the patch.
 *   4. Patched `src/app.py` verified:
 *      - contains `instrument_client(OpenAI(...))`
 *      - contains `autocontext.integrations.openai` import
 *      - original `from openai import OpenAI` preserved
 *
 * Full path (TypeScript):
 *   1. Scratch repo: `src/client.ts` with `import { OpenAI } from "openai"; const c = new OpenAI()`
 *   2. Config file registers @autoctx/detector-openai-ts (the real plugin).
 *   3. `runInstrumentCommand(["--apply", "--force"])` applies the patch.
 *   4. Patched `src/client.ts` verified:
 *      - contains `instrumentClient(new OpenAI(...))`
 *      - contains `autoctx/integrations/openai` import
 *      - original `import { OpenAI }` preserved
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
import { __resetForTests as resetTreeSitterCache } from "../../src/control-plane/instrument/scanner/tree-sitter-loader.js";

const __dirname = dirname(fileURLToPath(import.meta.url));

// Absolute file:// URL to the real plugin sources.
const OPENAI_PYTHON_PLUGIN_PATH = resolve(
  __dirname,
  "../../src/control-plane/instrument/detectors/openai-python/index.js",
);
const OPENAI_TS_PLUGIN_PATH = resolve(
  __dirname,
  "../../src/control-plane/instrument/detectors/openai-ts/index.js",
);
const OPENAI_PYTHON_PLUGIN_URL = pathToFileURL(OPENAI_PYTHON_PLUGIN_PATH).href;
const OPENAI_TS_PLUGIN_URL = pathToFileURL(OPENAI_TS_PLUGIN_PATH).href;

// Absolute file:// URL to the plugin registry.
const REGISTRY_PATH = resolve(
  __dirname,
  "../../src/control-plane/instrument/registry/plugin-registry.js",
);
const REGISTRY_URL = pathToFileURL(REGISTRY_PATH).href;

const scratches: string[] = [];

function scratch(): string {
  const d = mkdtempSync(join(tmpdir(), "autoctx-e2e-real-"));
  scratches.push(d);
  return d;
}

/** Config file content that registers the real openai-python detector plugin. */
function pythonConfigFileSrc(): string {
  return [
    `import { plugin } from ${JSON.stringify(OPENAI_PYTHON_PLUGIN_URL)};`,
    `import { registerDetectorPlugin } from ${JSON.stringify(REGISTRY_URL)};`,
    `registerDetectorPlugin(plugin);`,
    "",
  ].join("\n");
}

/** Config file content that registers the real openai-ts detector plugin. */
function tsConfigFileSrc(): string {
  return [
    `import { plugin } from ${JSON.stringify(OPENAI_TS_PLUGIN_URL)};`,
    `import { registerDetectorPlugin } from ${JSON.stringify(REGISTRY_URL)};`,
    `registerDetectorPlugin(plugin);`,
    "",
  ].join("\n");
}

beforeEach(() => {
  resetRegistryForTests();
  resetTreeSitterCache();
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

describe("E2E real-plugin: config-file → instrument --apply → patched Python file", () => {
  test(
    "real openai-python plugin patches src/app.py",
    async () => {
      const cwd = scratch();
      mkdirSync(join(cwd, "src"), { recursive: true });
      const originalPy = "from openai import OpenAI\nclient = OpenAI()\n";
      writeFileSync(join(cwd, "src", "app.py"), originalPy, "utf-8");
      writeFileSync(join(cwd, ".autoctx.instrument.config.mjs"), pythonConfigFileSrc(), "utf-8");

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
    45_000,
  );

  test(
    "real openai-python plugin dry-run: does not modify src/app.py",
    async () => {
      const cwd = scratch();
      mkdirSync(join(cwd, "src"), { recursive: true });
      const originalPy = "from openai import OpenAI\nclient = OpenAI()\n";
      writeFileSync(join(cwd, "src", "app.py"), originalPy, "utf-8");
      writeFileSync(join(cwd, ".autoctx.instrument.config.mjs"), pythonConfigFileSrc(), "utf-8");

      const result = await runInstrumentCommand(["--output", "json"], { cwd });

      expect(result.exitCode).toBe(0);
      const payload = JSON.parse(result.stdout);
      expect(payload.mode).toBe("dry-run");
      expect(payload.filesAffected).toBeGreaterThanOrEqual(1);

      // File must NOT be modified in dry-run.
      const unchanged = readFileSync(join(cwd, "src", "app.py"), "utf-8");
      expect(unchanged).toBe(originalPy);
    },
    45_000,
  );

  test(
    "real openai-python plugin: file without openai import → no edit",
    async () => {
      const cwd = scratch();
      mkdirSync(join(cwd, "src"), { recursive: true });
      const originalPy = "# no openai import\nclient = OpenAI()\n";
      writeFileSync(join(cwd, "src", "other.py"), originalPy, "utf-8");
      writeFileSync(join(cwd, ".autoctx.instrument.config.mjs"), pythonConfigFileSrc(), "utf-8");

      const result = await runInstrumentCommand(["--output", "json"], { cwd });

      expect(result.exitCode).toBe(0);
      const payload = JSON.parse(result.stdout);
      expect(payload.filesAffected).toBe(0);

      // File must NOT be modified.
      const unchanged = readFileSync(join(cwd, "src", "other.py"), "utf-8");
      expect(unchanged).toBe(originalPy);
    },
    45_000,
  );
});

describe("E2E real-plugin: config-file → instrument --apply → patched TypeScript file", () => {
  test(
    "real openai-ts plugin patches src/client.ts",
    async () => {
      const cwd = scratch();
      mkdirSync(join(cwd, "src"), { recursive: true });
      const originalTs = 'import { OpenAI } from "openai";\nconst client = new OpenAI();\n';
      writeFileSync(join(cwd, "src", "client.ts"), originalTs, "utf-8");
      writeFileSync(join(cwd, ".autoctx.instrument.config.mjs"), tsConfigFileSrc(), "utf-8");

      const result = await runInstrumentCommand(
        ["--apply", "--force", "--output", "json"],
        { cwd },
      );

      expect(result.exitCode).toBe(0);
      const payload = JSON.parse(result.stdout);
      expect(payload.mode).toBe("apply");
      expect(payload.filesAffected).toBeGreaterThanOrEqual(1);
      expect(payload.applyResult).toBeDefined();

      const patched = readFileSync(join(cwd, "src", "client.ts"), "utf-8");

      // Wrap must be applied.
      expect(patched).toContain("instrumentClient(");
      expect(patched).toContain("instrumentClient(new OpenAI(");

      // Import injection from autoctx/integrations/openai.
      expect(patched).toContain("autoctx/integrations/openai");

      // Original openai import must be preserved.
      expect(patched).toContain('from "openai"');

      // Patched content must differ from original.
      expect(patched).not.toBe(originalTs);
    },
    45_000,
  );

  test(
    "real openai-ts plugin dry-run: does not modify src/client.ts",
    async () => {
      const cwd = scratch();
      mkdirSync(join(cwd, "src"), { recursive: true });
      const originalTs = 'import { OpenAI } from "openai";\nconst client = new OpenAI();\n';
      writeFileSync(join(cwd, "src", "client.ts"), originalTs, "utf-8");
      writeFileSync(join(cwd, ".autoctx.instrument.config.mjs"), tsConfigFileSrc(), "utf-8");

      const result = await runInstrumentCommand(["--output", "json"], { cwd });

      expect(result.exitCode).toBe(0);
      const payload = JSON.parse(result.stdout);
      expect(payload.mode).toBe("dry-run");
      expect(payload.filesAffected).toBeGreaterThanOrEqual(1);

      // File must NOT be modified in dry-run.
      const unchanged = readFileSync(join(cwd, "src", "client.ts"), "utf-8");
      expect(unchanged).toBe(originalTs);
    },
    45_000,
  );
});
