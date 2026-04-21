/**
 * Fixture DetectorPlugin - emits InsertStatementEdit at the top of any Python file
 * containing a configurable anchor string. Exercises indentation-matcher + anchor
 * semantics in the planner/pipeline.
 */
import type {
  DetectorPlugin,
  InsertStatementEdit,
  PluginProduceResult,
  SourceFile,
  TreeSitterMatch,
} from "../../../src/control-plane/instrument/contract/plugin-interface.js";

export const mockInsertStatementPlugin: DetectorPlugin = {
  id: "mock-insert-statement",
  supports: { language: "python", sdkName: "mock-insert" },
  treeSitterQueries: ["(module) @m"],
  produce(_match: TreeSitterMatch, sourceFile: SourceFile): PluginProduceResult {
    const text = sourceFile.bytes.toString("utf-8");
    const anchor = text.indexOf("ANCHOR_HERE");
    if (anchor === -1) return { edits: [], advisories: [] };
    const endByte = anchor + "ANCHOR_HERE".length;
    const before = text.slice(0, anchor);
    const line = (before.match(/\n/g)?.length ?? 0) + 1;
    const col = anchor - (before.lastIndexOf("\n") + 1);
    const endBefore = text.slice(0, endByte);
    const endLine = (endBefore.match(/\n/g)?.length ?? 0) + 1;
    const endCol = endByte - (endBefore.lastIndexOf("\n") + 1);
    const edit: InsertStatementEdit = {
      kind: "insert-statement",
      pluginId: "mock-insert-statement",
      sourceFilePath: sourceFile.path,
      importsNeeded: [],
      anchor: {
        kind: "after",
        range: {
          startByte: anchor,
          endByte,
          startLineCol: { line, col },
          endLineCol: { line: endLine, col: endCol },
        },
      },
      statementSource: "autocontext.init()",
    };
    return { edits: [edit], advisories: [] };
  },
};
