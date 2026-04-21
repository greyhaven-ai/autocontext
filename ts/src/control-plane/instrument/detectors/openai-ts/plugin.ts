/**
 * A2-II-b openai-ts detector.
 *
 * Detects TypeScript/JavaScript `new OpenAI(...)` and `new AsyncOpenAI(...)`
 * constructor expressions and emits wrap-expression edits to instrument them
 * via `instrumentClient(...)`.
 *
 * Gates (processed in order):
 *   Gate 1: Import resolution — the ctor must be importable from the openai module.
 *   Gate 2: Idempotency — already wrapped by instrumentClient → advisory.
 *   Gate 3: Factory function — returned from a function body → advisory (deferred).
 */
import type {
  DetectorPlugin,
  EditDescriptor,
  PluginAdvisory,
  PluginProduceResult,
  SourceFile,
  SourceRange,
  TreeSitterMatch,
} from "../../contract/plugin-interface.js";
import { resolveLocalName } from "../../contract/plugin-interface.js";

const PLUGIN_ID = "@autoctx/detector-openai-ts";

function rangeOfCaptureNode(node: { startIndex: number; endIndex: number }, bytes: Buffer): SourceRange {
  const startByte = node.startIndex;
  const endByte = node.endIndex;
  const src = bytes.toString("utf-8");
  const pre = src.slice(0, startByte);
  const preE = src.slice(0, endByte);
  const startLine = (pre.match(/\n/g)?.length ?? 0) + 1;
  const startCol = startByte - (pre.lastIndexOf("\n") + 1);
  const endLine = (preE.match(/\n/g)?.length ?? 0) + 1;
  const endCol = endByte - (preE.lastIndexOf("\n") + 1);
  return {
    startByte,
    endByte,
    startLineCol: { line: startLine, col: startCol },
    endLineCol: { line: endLine, col: endCol },
  };
}

function isAlreadyWrapped(sourceFile: SourceFile, callRange: SourceRange): boolean {
  // Walk backward from callRange.startByte looking for `instrumentClient(` with
  // only whitespace between the `(` and callRange.startByte.
  const before = sourceFile.bytes.slice(0, callRange.startByte).toString("utf-8");
  const re = /instrumentClient\s*\(\s*$/;
  return re.test(before);
}

function isFactoryReturn(sourceFile: SourceFile, callRange: SourceRange): boolean {
  const src = sourceFile.bytes.toString("utf-8");
  const lineStart = src.lastIndexOf("\n", callRange.startByte - 1) + 1;
  const lineEnd = src.indexOf("\n", lineStart);
  const lineText = src.slice(lineStart, lineEnd === -1 ? undefined : lineEnd).trimStart();
  if (!lineText.startsWith("return ")) return false;
  // Conservative: check return keyword appears before the call start on the same line.
  const returnExprStart = lineStart + src.slice(lineStart).indexOf("return ") + "return ".length;
  return returnExprStart <= callRange.startByte;
}

function emitWrap(range: SourceRange, sourceFilePath: string): EditDescriptor[] {
  return [
    {
      kind: "wrap-expression",
      pluginId: PLUGIN_ID,
      sourceFilePath,
      range,
      wrapFn: "instrumentClient",
      wrapArgsBefore: [],
      wrapArgsAfter: [],
      importsNeeded: [{ module: "autoctx/integrations/openai", name: "instrumentClient", kind: "named" }],
      notes: ["OpenAI client wrapped; pass sink: ... at the wrap site."],
    },
    {
      kind: "insert-statement",
      pluginId: PLUGIN_ID,
      sourceFilePath,
      anchor: { kind: "before", range },
      statementSource: "// autocontext: configure the sink for this client — see docs/integrations/openai.md",
      importsNeeded: [],
    },
  ];
}

export const plugin: DetectorPlugin = {
  id: PLUGIN_ID,
  supports: { language: "typescript", sdkName: "openai" },
  treeSitterQueries: [
    "(new_expression constructor: (identifier) @ctor) @call",
    "(new_expression constructor: (member_expression object: (identifier) @mod property: (property_identifier) @ctor)) @call",
  ],
  produce(match: TreeSitterMatch, sourceFile: SourceFile): PluginProduceResult {
    const edits: EditDescriptor[] = [];
    const advisories: PluginAdvisory[] = [];

    const callCapture = match.captures.find((c) => c.name === "call");
    const ctorCapture = match.captures.find((c) => c.name === "ctor");
    const modCapture = match.captures.find((c) => c.name === "mod");

    if (!callCapture || !ctorCapture) {
      return { edits, advisories };
    }

    const callNode = callCapture.node;
    const ctorNode = ctorCapture.node;
    const modNode = modCapture?.node;

    const ctorText = sourceFile.bytes.slice(ctorNode.startIndex, ctorNode.endIndex).toString("utf-8");
    const callRange = rangeOfCaptureNode(callNode, sourceFile.bytes);

    // Find the openai-module import.
    const openaiImport = Array.from(sourceFile.existingImports).find((i) => i.module === "openai");

    // Module-prefixed query path: `openai.OpenAI` or `oa.OpenAI` (namespace import)
    if (modNode) {
      const modText = sourceFile.bytes.slice(modNode.startIndex, modNode.endIndex).toString("utf-8");

      // Check whether any openai import entry has an alias matching modText
      // `import * as openai from "openai"` → name="openai", alias="openai"
      // `import * as oa from "openai"` → name="openai", alias="oa"
      const openaiAliases = Array.from(openaiImport?.names ?? []).filter((n) => n.name === "openai");
      if (!openaiAliases.some((n) => (n.alias ?? n.name) === modText)) {
        advisories.push({
          pluginId: PLUGIN_ID,
          sourceFilePath: sourceFile.path,
          range: callRange,
          kind: "unresolved-import",
          reason: `\`import * as openai from "openai"\` (or alias \`${modText}\`) not found in file`,
        });
        return { edits, advisories };
      }

      if (ctorText === "AzureOpenAI") {
        advisories.push({
          pluginId: PLUGIN_ID,
          sourceFilePath: sourceFile.path,
          range: callRange,
          kind: "deferred-sdk-variant",
          reason: "AzureOpenAI deferred to a2-ii-b-azure; wrap manually: instrumentClient(new openai.AzureOpenAI(...))",
        });
        return { edits, advisories };
      }

      if (ctorText !== "OpenAI" && ctorText !== "AsyncOpenAI") {
        // Not a target constructor — not our concern.
        return { edits, advisories };
      }

      // Gate 2: idempotency
      if (isAlreadyWrapped(sourceFile, callRange)) {
        advisories.push({
          pluginId: PLUGIN_ID,
          sourceFilePath: sourceFile.path,
          range: callRange,
          kind: "already-wrapped",
          reason: "call site is already wrapped by instrumentClient()",
        });
        return { edits, advisories };
      }

      // Gate 3: factory function
      if (isFactoryReturn(sourceFile, callRange)) {
        advisories.push({
          pluginId: PLUGIN_ID,
          sourceFilePath: sourceFile.path,
          range: callRange,
          kind: "factoryFunction",
          reason: "call is the return expression of a factory function; wrap at the call site of the factory instead",
        });
        return { edits, advisories };
      }

      return { edits: emitWrap(callRange, sourceFile.path), advisories };
    }

    // Canonical query path: ctor must resolve via existingImports.
    // Handles: `import { OpenAI } from "openai"` (named import)
    //          `import { OpenAI as Foo } from "openai"` (aliased named import)
    //          `import OpenAI from "openai"` (default import → name="default", alias="OpenAI")
    if (!openaiImport) {
      advisories.push({
        pluginId: PLUGIN_ID,
        sourceFilePath: sourceFile.path,
        range: callRange,
        kind: "unresolved-import",
        reason: `${ctorText} referenced but openai not imported`,
      });
      return { edits, advisories };
    }

    const resolved = resolveLocalName(openaiImport.names, ctorText);
    if (resolved === undefined) {
      advisories.push({
        pluginId: PLUGIN_ID,
        sourceFilePath: sourceFile.path,
        range: callRange,
        kind: "unresolved-import",
        reason: `${ctorText} not imported from openai`,
      });
      return { edits, advisories };
    }

    if (resolved === "AzureOpenAI") {
      advisories.push({
        pluginId: PLUGIN_ID,
        sourceFilePath: sourceFile.path,
        range: callRange,
        kind: "deferred-sdk-variant",
        reason: "AzureOpenAI deferred to a2-ii-b-azure; wrap manually: instrumentClient(new AzureOpenAI(...))",
      });
      return { edits, advisories };
    }

    if (resolved !== "OpenAI" && resolved !== "AsyncOpenAI") {
      return { edits, advisories };
    }

    // Gate 2: idempotency
    if (isAlreadyWrapped(sourceFile, callRange)) {
      advisories.push({
        pluginId: PLUGIN_ID,
        sourceFilePath: sourceFile.path,
        range: callRange,
        kind: "already-wrapped",
        reason: "call site is already wrapped by instrumentClient()",
      });
      return { edits, advisories };
    }

    // Gate 3: factory function
    if (isFactoryReturn(sourceFile, callRange)) {
      advisories.push({
        pluginId: PLUGIN_ID,
        sourceFilePath: sourceFile.path,
        range: callRange,
        kind: "factoryFunction",
        reason: "call is the return expression of a factory function; wrap at the call site of the factory instead",
      });
      return { edits, advisories };
    }

    return { edits: emitWrap(callRange, sourceFile.path), advisories };
  },
};
