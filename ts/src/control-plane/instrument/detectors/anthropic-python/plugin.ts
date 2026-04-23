/**
 * A2-III anthropic-python detector.
 *
 * Detects Python `Anthropic(...)` and `AsyncAnthropic(...)` constructor calls
 * and emits wrap-expression edits to instrument them via `instrument_client(...)`.
 *
 * Gates (processed in order):
 *   Gate 1: Import resolution — the ctor must be importable from the anthropic module.
 *   Gate 2: Idempotency — already wrapped by instrument_client → advisory.
 *   Gate 3: Factory function — returned from a `def` → advisory (deferred).
 *
 * AnthropicBedrock and AnthropicVertex are refused via deferred-sdk-variant advisories.
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

const PLUGIN_ID = "@autoctx/detector-anthropic-python";
const ANTHROPIC_QUICKSTART_URL =
  "https://github.com/greyhaven-ai/autocontext/tree/main/autocontext#anthropic-integration";

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
  const before = sourceFile.bytes.slice(0, callRange.startByte).toString("utf-8");
  const re = /instrument_client\s*\(\s*$/;
  return re.test(before);
}

function isFactoryReturn(sourceFile: SourceFile, callRange: SourceRange): boolean {
  const src = sourceFile.bytes.toString("utf-8");
  const lineStart = src.lastIndexOf("\n", callRange.startByte - 1) + 1;
  const lineEnd = src.indexOf("\n", lineStart);
  const lineText = src.slice(lineStart, lineEnd === -1 ? undefined : lineEnd).trimStart();
  if (!lineText.startsWith("return ")) return false;
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
      wrapFn: "instrument_client",
      wrapArgsBefore: [],
      wrapArgsAfter: [],
      importsNeeded: [{ module: "autocontext.integrations.anthropic", name: "instrument_client", kind: "named" }],
      notes: ["Anthropic client wrapped; pass sink=... at the wrap site."],
    },
    {
      kind: "insert-statement",
      pluginId: PLUGIN_ID,
      sourceFilePath,
      anchor: { kind: "before", range },
      statementSource: `# autocontext: configure the sink for this client — see ${ANTHROPIC_QUICKSTART_URL}`,
      importsNeeded: [],
    },
  ];
}

export const plugin: DetectorPlugin = {
  id: PLUGIN_ID,
  supports: { language: "python", sdkName: "anthropic" },
  treeSitterQueries: [
    "(call function: (identifier) @ctor) @call",
    "(call function: (attribute object: (identifier) @mod attribute: (identifier) @ctor)) @call",
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

    const anthropicImport = Array.from(sourceFile.existingImports).find((i) => i.module === "anthropic");

    // Module-prefixed query path: `anthropic.Anthropic()` or `ac.Anthropic()`
    if (modNode) {
      const modText = sourceFile.bytes.slice(modNode.startIndex, modNode.endIndex).toString("utf-8");

      const anthropicAliases = Array.from(anthropicImport?.names ?? []).filter((n) => n.name === "anthropic");
      if (!anthropicAliases.some((n) => (n.alias ?? n.name) === modText)) {
        advisories.push({
          pluginId: PLUGIN_ID,
          sourceFilePath: sourceFile.path,
          range: callRange,
          kind: "unresolved-import",
          reason: `\`import anthropic\` (or alias \`${modText}\`) not found in file`,
        });
        return { edits, advisories };
      }

      if (ctorText === "AnthropicBedrock") {
        advisories.push({
          pluginId: PLUGIN_ID,
          sourceFilePath: sourceFile.path,
          range: callRange,
          kind: "deferred-sdk-variant",
          reason: "AnthropicBedrock deferred to a2-iii-bedrock; wrap manually: instrument_client(anthropic.AnthropicBedrock(...))",
        });
        return { edits, advisories };
      }

      if (ctorText === "AnthropicVertex") {
        advisories.push({
          pluginId: PLUGIN_ID,
          sourceFilePath: sourceFile.path,
          range: callRange,
          kind: "deferred-sdk-variant",
          reason: "AnthropicVertex deferred to a2-iii-vertex; wrap manually: instrument_client(anthropic.AnthropicVertex(...))",
        });
        return { edits, advisories };
      }

      if (ctorText !== "Anthropic" && ctorText !== "AsyncAnthropic") {
        return { edits, advisories };
      }

      // Gate 2: idempotency
      if (isAlreadyWrapped(sourceFile, callRange)) {
        advisories.push({
          pluginId: PLUGIN_ID,
          sourceFilePath: sourceFile.path,
          range: callRange,
          kind: "already-wrapped",
          reason: "call site is already wrapped by instrument_client()",
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
    if (!anthropicImport) {
      advisories.push({
        pluginId: PLUGIN_ID,
        sourceFilePath: sourceFile.path,
        range: callRange,
        kind: "unresolved-import",
        reason: `${ctorText} referenced but anthropic not imported`,
      });
      return { edits, advisories };
    }

    const resolved = resolveLocalName(anthropicImport.names, ctorText);
    if (resolved === undefined) {
      advisories.push({
        pluginId: PLUGIN_ID,
        sourceFilePath: sourceFile.path,
        range: callRange,
        kind: "unresolved-import",
        reason: `${ctorText} not imported from anthropic`,
      });
      return { edits, advisories };
    }

    if (resolved === "AnthropicBedrock") {
      advisories.push({
        pluginId: PLUGIN_ID,
        sourceFilePath: sourceFile.path,
        range: callRange,
        kind: "deferred-sdk-variant",
        reason: "AnthropicBedrock deferred to a2-iii-bedrock; wrap manually: instrument_client(AnthropicBedrock(...))",
      });
      return { edits, advisories };
    }

    if (resolved === "AnthropicVertex") {
      advisories.push({
        pluginId: PLUGIN_ID,
        sourceFilePath: sourceFile.path,
        range: callRange,
        kind: "deferred-sdk-variant",
        reason: "AnthropicVertex deferred to a2-iii-vertex; wrap manually: instrument_client(AnthropicVertex(...))",
      });
      return { edits, advisories };
    }

    if (resolved !== "Anthropic" && resolved !== "AsyncAnthropic") {
      return { edits, advisories };
    }

    // Gate 2: idempotency
    if (isAlreadyWrapped(sourceFile, callRange)) {
      advisories.push({
        pluginId: PLUGIN_ID,
        sourceFilePath: sourceFile.path,
        range: callRange,
        kind: "already-wrapped",
        reason: "call site is already wrapped by instrument_client()",
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
