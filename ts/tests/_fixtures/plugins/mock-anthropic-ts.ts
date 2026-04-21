/**
 * Fixture DetectorPlugin - detects TypeScript Anthropic client construction.
 * Not shipped in the CLI bundle.
 */
import type {
  DetectorPlugin,
  PluginProduceResult,
  SourceFile,
  TreeSitterMatch,
  WrapExpressionEdit,
} from "../../../src/control-plane/instrument/contract/plugin-interface.js";

export const mockAnthropicTsPlugin: DetectorPlugin = {
  id: "mock-anthropic-ts",
  supports: { language: "typescript", sdkName: "anthropic" },
  treeSitterQueries: ["(new_expression) @new"],
  produce(_match: TreeSitterMatch, sourceFile: SourceFile): PluginProduceResult {
    if (sourceFile.language !== "typescript" && sourceFile.language !== "tsx") return { edits: [], advisories: [] };
    const text = sourceFile.bytes.toString("utf-8");
    return { edits: findAnthropicCalls(text, sourceFile.path), advisories: [] };
  },
};

function findAnthropicCalls(text: string, filePath: string): readonly WrapExpressionEdit[] {
  const results: WrapExpressionEdit[] = [];
  const re = /\bnew\s+Anthropic\(/g;
  let m: RegExpExecArray | null;
  while ((m = re.exec(text)) !== null) {
    const start = m.index;
    const openParen = start + m[0].length - 1;
    const end = findMatchingParen(text, openParen);
    if (end === -1) continue;
    const endByte = end + 1;
    results.push({
      kind: "wrap-expression",
      pluginId: "mock-anthropic-ts",
      sourceFilePath: filePath,
      importsNeeded: [
        { module: "@autocontext/anthropic", name: "instrumentClient", kind: "named" },
      ],
      range: rangeFromBytes(text, start, endByte),
      wrapFn: "instrumentClient",
    });
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
