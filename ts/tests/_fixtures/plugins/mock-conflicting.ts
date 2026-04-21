/**
 * Fixture DetectorPlugin - intentionally conflicts with mock-openai-python by
 * wrapping the same OpenAI(...) range with a different wrapFn.
 *
 * Used to drive the conflict detector end-to-end through the pipeline
 * (same-range-different-wrapfn path, exit code 13).
 */
import type {
  DetectorPlugin,
  PluginProduceResult,
  SourceFile,
  TreeSitterMatch,
  WrapExpressionEdit,
} from "../../../src/control-plane/instrument/contract/plugin-interface.js";

export const mockConflictingPlugin: DetectorPlugin = {
  id: "mock-conflicting",
  supports: { language: "python", sdkName: "openai-alternate" },
  treeSitterQueries: ["(call) @call"],
  produce(_match: TreeSitterMatch, sourceFile: SourceFile): PluginProduceResult {
    if (sourceFile.language !== "python") return { edits: [], advisories: [] };
    const text = sourceFile.bytes.toString("utf-8");
    return { edits: findOpenAiCalls(text, sourceFile.path), advisories: [] };
  },
};

function findOpenAiCalls(text: string, filePath: string): readonly WrapExpressionEdit[] {
  const results: WrapExpressionEdit[] = [];
  const needle = "OpenAI(";
  let idx = text.indexOf(needle, 0);
  while (idx !== -1) {
    const start = idx;
    const openParen = start + needle.length - 1;
    const end = findMatchingParen(text, openParen);
    if (end !== -1) {
      const endByte = end + 1;
      results.push({
        kind: "wrap-expression",
        pluginId: "mock-conflicting",
        sourceFilePath: filePath,
        importsNeeded: [],
        range: rangeFromBytes(text, start, endByte),
        // Deliberately different wrapFn from mock-openai-python's "instrument_client".
        wrapFn: "alternative_instrument",
      });
    }
    idx = text.indexOf(needle, idx + 1);
  }
  return results;
}

function findMatchingParen(text: string, openIdx: number): number {
  let depth = 0;
  for (let i = openIdx; i < text.length; i += 1) {
    const c = text[i];
    if (c === "(") depth += 1;
    else if (c === ")") {
      depth -= 1;
      if (depth === 0) return i;
    }
  }
  return -1;
}

function rangeFromBytes(text: string, startByte: number, endByte: number): WrapExpressionEdit["range"] {
  const before = text.slice(0, startByte);
  const sLine = (before.match(/\n/g)?.length ?? 0) + 1;
  const sLastNl = before.lastIndexOf("\n");
  const sCol = startByte - (sLastNl + 1);
  const between = text.slice(0, endByte);
  const eLine = (between.match(/\n/g)?.length ?? 0) + 1;
  const eLastNl = between.lastIndexOf("\n");
  const eCol = endByte - (eLastNl + 1);
  return {
    startByte,
    endByte,
    startLineCol: { line: sLine, col: sCol },
    endLineCol: { line: eLine, col: eCol },
  };
}
