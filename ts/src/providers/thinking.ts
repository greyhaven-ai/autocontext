import type {
  CompletionResult,
  LLMProvider,
  ThinkingCompletionOptions,
} from "../types/index.js";

export const DEEP_THINK_TOOL_NAME = "deep_think";
export const DEEP_THINK_DESCRIPTION =
  "Record private scratchpad reasoning before producing the final answer.";
export const DEEP_THINK_PARAMETERS: Record<string, unknown> = {
  type: "object",
  properties: { thoughts: { type: "string" } },
  required: ["thoughts"],
  additionalProperties: false,
};

const DEEP_THINK_SYSTEM_SUFFIX =
  "Use the deep_think tool as a private scratchpad before answering. Its arguments are captured " +
  "separately and are not part of the final answer. Call it again only for materially new reasoning, " +
  "then put only the requested deliverable in the final response.";

export function withDeepThinkInstruction(systemPrompt: string): string {
  return `${systemPrompt.trimEnd()}\n\n${DEEP_THINK_SYSTEM_SUFFIX}`.trim();
}

export function extractDeepThought(payload: unknown): string {
  if (typeof payload === "string") {
    try {
      return extractDeepThought(JSON.parse(payload));
    } catch {
      return payload.trim() || "<empty deep_think arguments>";
    }
  }
  if (isRecord(payload) && typeof payload["thoughts"] === "string") {
    return payload["thoughts"];
  }
  return stableJson(payload);
}

export function deepThinkAcknowledgement(index: number): string {
  return JSON.stringify({
    recorded: index,
    next: "Resolve another material reasoning step with deep_think, or provide the final answer now.",
  });
}

export function addCompletionUsage(
  total: Record<string, number>,
  usage: Record<string, number> | null | undefined,
): void {
  if (!usage) return;
  for (const [key, value] of Object.entries(usage)) {
    if (Number.isFinite(value)) total[key] = (total[key] ?? 0) + value;
  }
}

/**
 * Invoke native thinking collection when available and otherwise report an
 * honest unsupported fallback without manufacturing a tool stream.
 */
export async function completeWithThinkingFallback(
  provider: LLMProvider,
  opts: ThinkingCompletionOptions,
): Promise<CompletionResult> {
  if (provider.completeWithThinking) return provider.completeWithThinking(opts);
  const result = await provider.complete(opts);
  return {
    ...result,
    thinkingStream: [],
    thinkingCapture: "unsupported",
  };
}

export function isRecord(value: unknown): value is Record<string, unknown> {
  return typeof value === "object" && value !== null && !Array.isArray(value);
}

function stableJson(value: unknown): string {
  if (!isRecord(value)) return JSON.stringify(value) ?? String(value);
  return JSON.stringify(
    Object.fromEntries(Object.entries(value).sort(([left], [right]) => left.localeCompare(right))),
  );
}
