/**
 * Golden-fixture test harness for the anthropic-ts detector.
 *
 * Regenerate with UPDATE_GOLDEN=1 npx vitest run tests/.../golden.test.ts
 */
import { describe, test, expect } from "vitest";
import {
  readdirSync,
  readFileSync,
  writeFileSync,
  existsSync,
} from "node:fs";
import { join, dirname } from "node:path";
import { fileURLToPath } from "node:url";
import { plugin } from "../../../../../src/control-plane/instrument/detectors/anthropic-ts/plugin.js";
import type { ImportedName, EditDescriptor, PluginAdvisory } from "../../../../../src/control-plane/instrument/contract/plugin-interface.js";

const __dirname = dirname(fileURLToPath(import.meta.url));
const GOLDEN_DIR = join(__dirname, "golden");
const UPDATE = process.env.UPDATE_GOLDEN === "1";

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
    language: "typescript",
    bytes,
    tree: null,
    directives: new Map(),
    hasSecretLiteral: false,
    secretMatches: [],
    existingImports,
    indentationStyle: { kind: "spaces", width: 2 },
  };
}

function runPlugin(sf: any): { edits: EditDescriptor[]; advisories: PluginAdvisory[] } {
  const text = (sf.bytes as Buffer).toString("utf-8");
  const allEdits: EditDescriptor[] = [];
  const allAdvisories: PluginAdvisory[] = [];

  const modCtorRe = /\bnew\s+(\w+)\.(Anthropic|AsyncAnthropic|AnthropicBedrock|AnthropicVertex)\s*\(/g;
  const modMatchedCtorStarts = new Set<number>();
  let m: RegExpExecArray | null;
  while ((m = modCtorRe.exec(text)) !== null) {
    const newStart = m.index;
    const modStart = newStart + 4;
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
    modMatchedCtorStarts.add(ctorStart);
    const match = {
      captures: [
        { name: "call", node: { startIndex: newStart, endIndex: callEnd } },
        { name: "mod", node: { startIndex: modStart, endIndex: modEnd } },
        { name: "ctor", node: { startIndex: ctorStart, endIndex: ctorEnd } },
      ],
    };
    const result = plugin.produce(match as any, sf);
    allEdits.push(...result.edits);
    allAdvisories.push(...result.advisories);
  }

  const ctorRe = /\bnew\s+(Anthropic|AsyncAnthropic|AnthropicBedrock|AnthropicVertex)\s*\(/g;
  while ((m = ctorRe.exec(text)) !== null) {
    const newStart = m.index;
    const ctorStart = newStart + 4;
    const ctorEnd = ctorStart + m[1]!.length;
    if (modMatchedCtorStarts.has(ctorStart)) continue;
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
        { name: "call", node: { startIndex: newStart, endIndex: callEnd } },
        { name: "ctor", node: { startIndex: ctorStart, endIndex: ctorEnd } },
      ],
    };
    const result = plugin.produce(match as any, sf);
    allEdits.push(...result.edits);
    allAdvisories.push(...result.advisories);
  }

  return { edits: allEdits, advisories: allAdvisories };
}

function assertGoldenJson(scenarioDir: string, filename: string, actual: unknown): void {
  const goldenPath = join(scenarioDir, filename);
  const actualJson = JSON.stringify(actual, null, 2) + "\n";
  if (UPDATE || !existsSync(goldenPath)) {
    writeFileSync(goldenPath, actualJson);
    if (!UPDATE) {
      throw new Error(`Golden ${filename} did not exist; wrote initial version. Re-run to verify.`);
    }
    return;
  }
  const expected = readFileSync(goldenPath, "utf-8");
  expect(actualJson).toBe(expected);
}

function serializeEdits(edits: EditDescriptor[]): unknown {
  return edits.map((e) => ({
    kind: e.kind,
    pluginId: e.pluginId,
    sourceFilePath: "(normalized)",
    range: e.kind !== "insert-statement" ? { startLineCol: (e as any).range.startLineCol, endLineCol: (e as any).range.endLineCol } : undefined,
    anchorRange: e.kind === "insert-statement" ? { startLineCol: e.anchor.range.startLineCol } : undefined,
    wrapFn: (e as any).wrapFn,
    importsNeeded: e.importsNeeded,
    statementSource: (e as any).statementSource,
  }));
}

function serializeAdvisories(advisories: PluginAdvisory[]): unknown {
  return advisories.map((a) => ({
    kind: a.kind,
    pluginId: a.pluginId,
    sourceFilePath: "(normalized)",
    reason: a.reason,
  }));
}

const scenarios = readdirSync(GOLDEN_DIR, { withFileTypes: true })
  .filter((d) => d.isDirectory())
  .map((d) => d.name)
  .sort();

describe("anthropic-ts detector golden fixtures", () => {
  for (const scenario of scenarios) {
    test(scenario, () => {
      const dir = join(GOLDEN_DIR, scenario);
      const inputPath = join(dir, "input.ts");
      const importsPath = join(dir, "existing-imports.json");

      const importsData: ImportEntry[] = JSON.parse(readFileSync(importsPath, "utf-8"));
      const sf = buildSourceFile(inputPath, importsData);
      const { edits, advisories } = runPlugin(sf);

      assertGoldenJson(dir, "expected-edits.json", serializeEdits(edits));
      assertGoldenJson(dir, "expected-advisories.json", serializeAdvisories(advisories));
    });
  }
});
