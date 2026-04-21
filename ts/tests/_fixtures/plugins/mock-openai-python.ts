/**
 * Fixture DetectorPlugin - detects Python OpenAI(...) calls.
 *
 * Not shipped in the CLI bundle (lives under tests/_fixtures/); used only in
 * the A2-I pipeline + CLI integration tests to exercise the full flow
 * end-to-end.
 *
 * Detection strategy: consume the tree-sitter `(call) @call` capture handed in
 * by the pipeline and wrap only OpenAI(...) call nodes.
 */
import type {
  DetectorPlugin,
  EditDescriptor,
  SourceFile,
  TreeSitterMatch,
  WrapExpressionEdit,
} from "../../../src/control-plane/instrument/contract/plugin-interface.js";

export const mockOpenAiPythonPlugin: DetectorPlugin = {
  id: "mock-openai-python",
  supports: { language: "python", sdkName: "openai" },
  treeSitterQueries: ["(call) @call"],
  produce(match: TreeSitterMatch, sourceFile: SourceFile): readonly EditDescriptor[] {
    if (sourceFile.language !== "python") return [];
    const call = match.captures.find((c) => c.name === "call");
    if (!call) return [];
    const text = sourceFile.bytes.subarray(call.node.startIndex, call.node.endIndex).toString("utf-8");
    if (!/^OpenAI\(/.test(text)) return [];
    return [
      {
      kind: "wrap-expression",
      pluginId: "mock-openai-python",
      sourceFilePath: sourceFile.path,
      importsNeeded: [
        { module: "autocontext.integrations.openai", name: "instrument_client", kind: "named" },
      ],
      range: rangeFromBytes(sourceFile.bytes, call.node.startIndex, call.node.endIndex),
      wrapFn: "instrument_client",
      },
    ];
  },
};

function rangeFromBytes(bytes: Buffer, startByte: number, endByte: number): WrapExpressionEdit["range"] {
  const before = bytes.subarray(0, startByte).toString("utf-8");
  const sLine = (before.match(/\n/g)?.length ?? 0) + 1;
  const sLastNl = before.lastIndexOf("\n");
  const sCol = before.length - (sLastNl + 1);
  const between = bytes.subarray(0, endByte).toString("utf-8");
  const eLine = (between.match(/\n/g)?.length ?? 0) + 1;
  const eLastNl = between.lastIndexOf("\n");
  const eCol = between.length - (eLastNl + 1);
  return {
    startByte,
    endByte,
    startLineCol: { line: sLine, col: sCol },
    endLineCol: { line: eLine, col: eCol },
  };
}
