/**
 * Fixture DetectorPlugin - detects Python OpenAI(...) calls.
 *
 * Not shipped in the CLI bundle (lives under tests/_fixtures/); used only in
 * the A2-I pipeline + CLI integration tests to exercise the full flow
 * end-to-end.
 *
 * Detection strategy: string-match against sourceFile.bytes for OpenAI(
 * followed by balanced parentheses. Tree-sitter queries are listed (non-empty)
 * so the pipeline invokes produce() once; the plugin does its own lookup
 * inside.
 */
import type {
  DetectorPlugin,
  PluginProduceResult,
  SourceFile,
  TreeSitterMatch,
  WrapExpressionEdit,
} from "../../../src/control-plane/instrument/contract/plugin-interface.js";

export const mockOpenAiPythonPlugin: DetectorPlugin = {
  id: "mock-openai-python",
  supports: { language: "python", sdkName: "openai" },
  treeSitterQueries: ["(call) @call"],
  produce(_match: TreeSitterMatch, sourceFile: SourceFile): PluginProduceResult {
    if (sourceFile.language !== "python") return { edits: [], advisories: [] };
    const text = sourceFile.bytes.toString("utf-8");
    return { edits: findOpenAiCalls(text, sourceFile.path), advisories: [] };
  },
};

function findOpenAiCalls(text: string, filePath: string): readonly WrapExpressionEdit[] {
  const results: WrapExpressionEdit[] = [];
  const re = /\bOpenAI\(/g;
  let m: RegExpExecArray | null;
  while ((m = re.exec(text)) !== null) {
    const start = m.index;
    const end = findMatchingParen(text, start + m[0].length - 1);
    if (end === -1) continue;
    const endByte = end + 1;
    results.push({
      kind: "wrap-expression",
      pluginId: "mock-openai-python",
      sourceFilePath: filePath,
      importsNeeded: [
        { module: "autocontext.integrations.openai", name: "instrument_client", kind: "named" },
      ],
      range: rangeFromBytes(text, start, endByte),
      wrapFn: "instrument_client",
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
