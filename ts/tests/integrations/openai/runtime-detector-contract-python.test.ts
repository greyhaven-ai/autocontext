/**
 * Runtime ↔ detector contract test: Python pair.
 *
 * Enumerates every `importsNeeded` name the openai-python detector plugin can
 * emit across its golden fixture corpus, then verifies each name is exported
 * by the Python runtime module `autocontext.integrations.openai`.
 *
 * Purpose: CI guard that catches drift between detector-emitted symbol names
 * and the runtime's actual public surface. If a future detector version adds
 * a new import name, this test fails until the runtime exports it too.
 */
import { describe, test, expect } from "vitest";
import { readdirSync, readFileSync } from "node:fs";
import { join, dirname } from "node:path";
import { fileURLToPath } from "node:url";
import { spawnSync } from "node:child_process";

import { plugin } from "../../../src/control-plane/instrument/detectors/openai-python/plugin.js";
import type { ImportedName } from "../../../src/control-plane/instrument/contract/plugin-interface.js";

const __dirname = dirname(fileURLToPath(import.meta.url));

// Golden fixtures live alongside the golden.test.ts for the openai-python detector
const GOLDEN_DIR = join(
  __dirname,
  "../../control-plane/instrument/detectors/openai-python/golden",
);

// Python package root — cwd for the subprocess
const PYTHON_PKG_ROOT = join(__dirname, "../../../../autocontext");

const RUNTIME_MODULE = "autocontext.integrations.openai";

interface ImportEntry {
  module: string;
  names: Array<{ name: string; alias?: string }>;
}

function buildSourceFile(inputPath: string, importsData: ImportEntry[]): any {
  const bytes = readFileSync(inputPath);
  const existingImports = new Set(
    importsData.map((entry) => ({
      module: entry.module,
      names: new Set<ImportedName>(
        entry.names.map((n) => ({ name: n.name, alias: n.alias })),
      ),
    })),
  );
  return {
    path: inputPath,
    language: "python",
    bytes,
    tree: null,
    directives: new Map(),
    hasSecretLiteral: false,
    secretMatches: [],
    existingImports,
    indentationStyle: { kind: "spaces", width: 4 },
  };
}

function collectImportsFromFixture(
  inputPath: string,
  importsData: ImportEntry[],
): Array<{ module: string; name: string }> {
  const sf = buildSourceFile(inputPath, importsData);
  const text = (sf.bytes as Buffer).toString("utf-8");
  const collected: Array<{ module: string; name: string }> = [];

  // Scan for module-prefixed calls first: openai.OpenAI( or oa.OpenAI(
  const modCtorRe = /\b(\w+)\.(OpenAI|AsyncOpenAI|AzureOpenAI)\s*\(/g;
  const modMatched = new Set<number>();
  let m: RegExpExecArray | null;
  while ((m = modCtorRe.exec(text)) !== null) {
    const modStart = m.index;
    const modEnd = modStart + m[1]!.length;
    const ctorStart = modEnd + 1;
    const ctorEnd = ctorStart + m[2]!.length;
    let depth = 0;
    let callEnd = ctorEnd;
    for (let i = ctorEnd; i < text.length; i++) {
      if (text[i] === "(") depth++;
      else if (text[i] === ")") {
        depth--;
        if (depth === 0) { callEnd = i + 1; break; }
      }
    }
    modMatched.add(ctorStart);
    const match = {
      captures: [
        { name: "call", node: { startIndex: modStart, endIndex: callEnd } },
        { name: "mod", node: { startIndex: modStart, endIndex: modEnd } },
        { name: "ctor", node: { startIndex: ctorStart, endIndex: ctorEnd } },
      ],
    };
    const result = plugin.produce(match as any, sf);
    for (const edit of result.edits) {
      for (const spec of edit.importsNeeded) {
        collected.push({ module: spec.module, name: spec.name });
      }
    }
  }

  // Scan for standalone ctors: OpenAI( or AsyncOpenAI( not preceded by a dot
  const ctorRe = /\b(OpenAI|AsyncOpenAI|AzureOpenAI)\s*\(/g;
  while ((m = ctorRe.exec(text)) !== null) {
    const ctorStart = m.index;
    if (modMatched.has(ctorStart)) continue;
    if (ctorStart > 0 && text[ctorStart - 1] === ".") continue;
    const ctorEnd = ctorStart + m[1]!.length;
    let depth = 0;
    let callEnd = ctorEnd;
    for (let i = ctorEnd; i < text.length; i++) {
      if (text[i] === "(") depth++;
      else if (text[i] === ")") {
        depth--;
        if (depth === 0) { callEnd = i + 1; break; }
      }
    }
    const match = {
      captures: [
        { name: "call", node: { startIndex: ctorStart, endIndex: callEnd } },
        { name: "ctor", node: { startIndex: ctorStart, endIndex: ctorEnd } },
      ],
    };
    const result = plugin.produce(match as any, sf);
    for (const edit of result.edits) {
      for (const spec of edit.importsNeeded) {
        collected.push({ module: spec.module, name: spec.name });
      }
    }
  }

  return collected;
}

describe("runtime↔detector contract: Python pair", () => {
  // Collect all importsNeeded names from every fixture
  const scenarios = readdirSync(GOLDEN_DIR, { withFileTypes: true })
    .filter((d) => d.isDirectory())
    .map((d) => d.name)
    .sort();

  const emittedNames = new Set<string>();

  for (const scenario of scenarios) {
    const dir = join(GOLDEN_DIR, scenario);
    const inputPath = join(dir, "input.py");
    const importsPath = join(dir, "existing-imports.json");

    const importsData: ImportEntry[] = JSON.parse(readFileSync(importsPath, "utf-8"));
    const specs = collectImportsFromFixture(inputPath, importsData);

    for (const spec of specs) {
      if (spec.module === RUNTIME_MODULE) {
        emittedNames.add(spec.name);
      }
    }
  }

  test("collects at least one import name from fixtures", () => {
    expect(
      emittedNames.size,
      "no import names collected from fixtures — detector may be broken",
    ).toBeGreaterThan(0);
  });

  test(`Python runtime exports every name emitted by the detector (${emittedNames.size} name(s))`, () => {
    // Spawn Python subprocess to enumerate runtime exports — args are hardcoded, no injection risk
    const proc = spawnSync(
      "uv",
      [
        "run",
        "python",
        "-c",
        "import autocontext.integrations.openai as m; import json; print(json.dumps(sorted(getattr(m, '__all__', dir(m)))))",
      ],
      {
        cwd: PYTHON_PKG_ROOT,
        encoding: "utf-8",
        timeout: 30_000,
      },
    );

    expect(
      proc.status,
      `Python subprocess failed (exit ${proc.status}):\n${proc.stderr}`,
    ).toBe(0);

    const exportedNames = new Set<string>(JSON.parse(proc.stdout.trim()) as string[]);

    for (const name of emittedNames) {
      expect(
        exportedNames.has(name),
        `runtime missing "${name}" — detector emits it but ${RUNTIME_MODULE} does not export it`,
      ).toBe(true);
    }
  });
});
