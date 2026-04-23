/**
 * A2-III anthropic-ts detector.
 *
 * Detects TypeScript/JavaScript `new Anthropic(...)` and `new AsyncAnthropic(...)`
 * constructor expressions and emits wrap-expression edits to instrument them
 * via `instrumentClient(...)`.
 *
 * Gates (processed in order):
 *   Gate 1: Import resolution — the ctor must be importable from @anthropic-ai/sdk.
 *   Gate 2: Idempotency — already wrapped by instrumentClient → advisory.
 *   Gate 3: Factory function — returned from a function body → advisory (deferred).
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

const PLUGIN_ID = "@autoctx/detector-anthropic-ts";
const ANTHROPIC_QUICKSTART_URL =
  "https://github.com/greyhaven-ai/autocontext/tree/main/ts#anthropic-integration";

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
  const re = /instrumentClient\s*\(\s*$/;
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
      wrapFn: "instrumentClient",
      wrapArgsBefore: [],
      wrapArgsAfter: [],
      importsNeeded: [{ module: "autoctx/integrations/anthropic", name: "instrumentClient", kind: "named" }],
      notes: ["Anthropic client wrapped; pass sink: ... at the wrap site."],
    },
    {
      kind: "insert-statement",
      pluginId: PLUGIN_ID,
      sourceFilePath,
      anchor: { kind: "before", range },
      statementSource: `// autocontext: configure the sink for this client — see ${ANTHROPIC_QUICKSTART_URL}`,
      importsNeeded: [],
    },
  ];
}

export const plugin: DetectorPlugin = {
  id: PLUGIN_ID,
  supports: { language: "typescript", sdkName: "anthropic" },
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

    const anthropicImport = Array.from(sourceFile.existingImports).find(
      (i) => i.module === "@anthropic-ai/sdk",
    );

    // Module-prefixed query path: `new anthropic.Anthropic(...)` or `new ac.Anthropic(...)`
    if (modNode) {
      const modText = sourceFile.bytes.slice(modNode.startIndex, modNode.endIndex).toString("utf-8");

      // `import * as anthropic from "@anthropic-ai/sdk"` → name="anthropic", alias="anthropic"
      // `import * as ac from "@anthropic-ai/sdk"` → name="anthropic", alias="ac"
      const anthropicAliases = Array.from(anthropicImport?.names ?? []).filter((n) => n.name === "anthropic");
      if (!anthropicAliases.some((n) => (n.alias ?? n.name) === modText)) {
        advisories.push({
          pluginId: PLUGIN_ID,
          sourceFilePath: sourceFile.path,
          range: callRange,
          kind: "unresolved-import",
          reason: `\`import * as anthropic from "@anthropic-ai/sdk"\` (or alias \`${modText}\`) not found in file`,
        });
        return { edits, advisories };
      }

      if (ctorText === "AnthropicBedrock") {
        advisories.push({
          pluginId: PLUGIN_ID,
          sourceFilePath: sourceFile.path,
          range: callRange,
          kind: "deferred-sdk-variant",
          reason: "AnthropicBedrock deferred to a2-iii-bedrock; wrap manually: instrumentClient(new anthropic.AnthropicBedrock(...))",
        });
        return { edits, advisories };
      }

      if (ctorText === "AnthropicVertex") {
        advisories.push({
          pluginId: PLUGIN_ID,
          sourceFilePath: sourceFile.path,
          range: callRange,
          kind: "deferred-sdk-variant",
          reason: "AnthropicVertex deferred to a2-iii-vertex; wrap manually: instrumentClient(new anthropic.AnthropicVertex(...))",
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
    // Handles: `import { Anthropic } from "@anthropic-ai/sdk"` (named import)
    //          `import { Anthropic as Foo } from "@anthropic-ai/sdk"` (aliased named import)
    if (!anthropicImport) {
      advisories.push({
        pluginId: PLUGIN_ID,
        sourceFilePath: sourceFile.path,
        range: callRange,
        kind: "unresolved-import",
        reason: `${ctorText} referenced but @anthropic-ai/sdk not imported`,
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
        reason: `${ctorText} not imported from @anthropic-ai/sdk`,
      });
      return { edits, advisories };
    }

    if (resolved === "AnthropicBedrock") {
      advisories.push({
        pluginId: PLUGIN_ID,
        sourceFilePath: sourceFile.path,
        range: callRange,
        kind: "deferred-sdk-variant",
        reason: "AnthropicBedrock deferred to a2-iii-bedrock; wrap manually: instrumentClient(new AnthropicBedrock(...))",
      });
      return { edits, advisories };
    }

    if (resolved === "AnthropicVertex") {
      advisories.push({
        pluginId: PLUGIN_ID,
        sourceFilePath: sourceFile.path,
        range: callRange,
        kind: "deferred-sdk-variant",
        reason: "AnthropicVertex deferred to a2-iii-vertex; wrap manually: instrumentClient(new AnthropicVertex(...))",
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
