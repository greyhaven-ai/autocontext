/**
 * Fixture DetectorPlugin - detects TypeScript Anthropic client construction.
 * Not shipped in the CLI bundle.
 */
import type {
  DetectorPlugin,
  EditDescriptor,
  SourceFile,
  TreeSitterMatch,
  WrapExpressionEdit,
} from "../../../src/control-plane/instrument/contract/plugin-interface.js";

export const mockAnthropicTsPlugin: DetectorPlugin = {
  id: "mock-anthropic-ts",
  supports: { language: "typescript", sdkName: "anthropic" },
  treeSitterQueries: ["(new_expression) @new"],
  produce(match: TreeSitterMatch, sourceFile: SourceFile): readonly EditDescriptor[] {
    if (sourceFile.language !== "typescript" && sourceFile.language !== "tsx") return [];
    const newExpression = match.captures.find((c) => c.name === "new");
    if (!newExpression) return [];
    const text = sourceFile.bytes.subarray(newExpression.node.startIndex, newExpression.node.endIndex).toString("utf-8");
    if (!/^new\s+Anthropic\(/.test(text)) return [];
    return [
      {
      kind: "wrap-expression",
      pluginId: "mock-anthropic-ts",
      sourceFilePath: sourceFile.path,
      importsNeeded: [
        { module: "@autocontext/anthropic", name: "instrumentClient", kind: "named" },
      ],
      range: rangeFromBytes(sourceFile.bytes, newExpression.node.startIndex, newExpression.node.endIndex),
      wrapFn: "instrumentClient",
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
