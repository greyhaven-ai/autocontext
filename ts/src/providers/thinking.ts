import type {
  CompletionResult,
  LLMProvider,
  ThinkingCompletionOptions,
} from "../types/index.js";
import { ProviderError } from "../types/index.js";

export const DEEP_THINK_TOOL_NAME = "deep_think";
export const DEEP_THINK_DESCRIPTION =
  "Record private scratchpad reasoning before producing the final answer.";
export const DEEP_THINK_PARAMETERS: Record<string, unknown> = {
  type: "object",
  properties: { thoughts: { type: "string" } },
  required: ["thoughts"],
  additionalProperties: false,
};

export const DEEP_THINK_JUICE_BY_EFFORT: Readonly<Record<string, number>> = {
  none: 0,
  minimal: 2,
  low: 4,
  medium: 8,
  high: 48,
  xhigh: 112,
  max: 960,
};

const DEEP_THINK_SYSTEM_SUFFIX =
  "Use the deep_think tool as a private scratchpad before drafting the final answer. Restate the task " +
  "and constraints, resolve the work in ordered steps, and check likely errors or boundary cases there. " +
  "Its arguments are captured separately and are not part of the final answer. Call it again only for " +
  "materially new reasoning, never to narrate progress, then put only the requested deliverable in the " +
  "final response.";

export function deepThinkJuice(reasoningEffort: string): number {
  return DEEP_THINK_JUICE_BY_EFFORT[reasoningEffort.trim().toLowerCase()] ?? 8;
}

export function withDeepThinkInstruction(systemPrompt: string, juice?: number): string {
  const instruction = `${systemPrompt.trimEnd()}\n\n${DEEP_THINK_SYSTEM_SUFFIX}`.trim();
  return juice === undefined ? instruction : `${instruction}\n\n# Juice: ${juice} !important`;
}

export function extractDeepThought(payload: unknown): string {
  if (typeof payload === "string") {
    let parsed: unknown;
    try {
      parsed = JSON.parse(payload);
    } catch {
      throw new ProviderError("Invalid deep_think arguments: arguments must be valid JSON");
    }
    return extractDeepThought(parsed);
  }
  if (!isRecord(payload)) {
    throw new ProviderError("Invalid deep_think arguments: arguments must be an object");
  }
  if (Object.keys(payload).length !== 1 || !("thoughts" in payload)) {
    throw new ProviderError(
      "Invalid deep_think arguments: arguments must contain only the required thoughts field",
    );
  }
  if (typeof payload["thoughts"] !== "string") {
    throw new ProviderError("Invalid deep_think arguments: thoughts must be a string");
  }
  return payload["thoughts"];
}

export function deepThinkAcknowledgement(index: number): string {
  return JSON.stringify({ recorded: index });
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
