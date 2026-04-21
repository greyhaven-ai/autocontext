/**
 * Fixture DetectorPlugin - intentionally conflicts with mock-openai-python by
 * wrapping the same OpenAI(...) range with a different wrapFn.
 *
 * Used to drive the conflict detector end-to-end through the pipeline
 * (same-range-different-wrapfn path, exit code 13).
 */
import type {
  DetectorPlugin,
  EditDescriptor,
  SourceFile,
  TreeSitterMatch,
  WrapExpressionEdit,
} from "../../../src/control-plane/instrument/contract/plugin-interface.js";

export const mockConflictingPlugin: DetectorPlugin = {
  id: "mock-conflicting",
  supports: { language: "python", sdkName: "openai-alternate" },
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
        pluginId: "mock-conflicting",
        sourceFilePath: sourceFile.path,
        importsNeeded: [],
        range: rangeFromBytes(sourceFile.bytes, call.node.startIndex, call.node.endIndex),
        // Deliberately different wrapFn from mock-openai-python's "instrument_client".
        wrapFn: "alternative_instrument",
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
