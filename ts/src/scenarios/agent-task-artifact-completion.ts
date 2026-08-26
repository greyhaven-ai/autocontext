import {
  completeWithProviderHooks,
  type HookedProviderCompletionOpts,
} from "../extensions/index.js";
import type { CompletionResult } from "../types/index.js";

/** Maximum output requested for each provider segment. */
export const AGENT_TASK_SEGMENT_MAX_TOKENS = 8_192;
/** Maximum number of follow-up segments after the first provider response. */
export const AGENT_TASK_MAX_CONTINUATIONS = 2;
/** Hard safety bound across the completed artifact, after overlap removal. */
export const AGENT_TASK_MAX_ACCUMULATED_CHARACTERS = 400_000;

export type AgentTaskArtifactCompletionOptions = Omit<HookedProviderCompletionOpts, "maxTokens"> & {
  /** Human-readable label used only in fail-closed errors. */
  artifactLabel: string;
};

function outputWasTruncated(result: CompletionResult): boolean {
  const stopReason =
    typeof result.stopReason === "string" ? result.stopReason.trim().toLowerCase() : undefined;
  return stopReason === "max_tokens" || stopReason === "length";
}

function addUsage(total: Record<string, number>, usage: unknown): void {
  if (typeof usage !== "object" || usage === null || Array.isArray(usage)) return;
  for (const [key, value] of Object.entries(usage)) {
    if (typeof value === "number" && Number.isFinite(value)) {
      total[key] = (total[key] ?? 0) + value;
    }
  }
}

/**
 * Return the longest bounded suffix/prefix match without quadratic scanning.
 * The bound prevents a provider from forcing unbounded overlap work by
 * repeating a very large artifact in a continuation.
 */
function continuationOverlap(existing: string, continuation: string): number {
  const maxLength = Math.min(existing.length, continuation.length, 16_384);
  if (maxLength === 0) return 0;

  const pattern = continuation.slice(0, maxLength);
  const suffix = existing.slice(-maxLength);
  const prefixLengths = new Array<number>(pattern.length).fill(0);

  for (let index = 1; index < pattern.length; index += 1) {
    let matched = prefixLengths[index - 1] ?? 0;
    while (matched > 0 && pattern[index] !== pattern[matched]) {
      matched = prefixLengths[matched - 1] ?? 0;
    }
    if (pattern[index] === pattern[matched]) matched += 1;
    prefixLengths[index] = matched;
  }

  let matched = 0;
  for (let index = 0; index < suffix.length; index += 1) {
    const character = suffix[index];
    while (matched > 0 && character !== pattern[matched]) {
      matched = prefixLengths[matched - 1] ?? 0;
    }
    if (character === pattern[matched]) matched += 1;
  }
  return Math.min(matched, maxLength);
}

function appendContinuation(existing: string, continuation: string): string {
  if (!existing) return continuation;
  // Some providers ignore the "new text only" instruction and return the
  // complete artifact. Accept that shape without duplicating the first part.
  if (continuation.startsWith(existing)) return continuation;
  const overlap = continuationOverlap(existing, continuation);
  return existing + continuation.slice(overlap);
}

function buildContinuationPrompt(
  originalPrompt: string,
  artifact: string,
  continuationNumber: number,
): string {
  return [
    "The previous response reached the provider output limit.",
    "Continue the artifact from exactly where it stopped and finish the original task.",
    "Return only new artifact text. Do not repeat, summarize, restart, or discuss the existing artifact.",
    "Do not mention continuation, segments, or token limits in the artifact.",
    "If the artifact ends mid-sentence, begin with the missing continuation without reprinting preceding words.",
    `This is bounded continuation ${continuationNumber} of ${AGENT_TASK_MAX_CONTINUATIONS}.`,
    "",
    "<original_task>",
    originalPrompt,
    "</original_task>",
    "",
    "<artifact_so_far>",
    artifact,
    "</artifact_so_far>",
  ].join("\n");
}

/**
 * Complete one agent-task artifact across a bounded number of provider
 * segments. A reported truncation never reaches evaluation or persistence as
 * if it were a complete artifact.
 */
export async function completeAgentTaskArtifact(
  opts: AgentTaskArtifactCompletionOptions,
): Promise<CompletionResult> {
  let accumulatedText = "";
  const accumulatedUsage: Record<string, number> = {};
  let accumulatedCostUsd = 0;
  let hasCost = false;

  for (let segment = 0; segment <= AGENT_TASK_MAX_CONTINUATIONS; segment += 1) {
    const continuationNumber = segment;
    const result = await completeWithProviderHooks({
      hookBus: opts.hookBus ?? null,
      provider: opts.provider,
      role: opts.role,
      systemPrompt:
        segment === 0
          ? opts.systemPrompt
          : `${opts.systemPrompt}\n\nContinue the existing artifact exactly at its cutoff. Return only new artifact text and finish the original task.`,
      userPrompt:
        segment === 0
          ? opts.userPrompt
          : buildContinuationPrompt(opts.userPrompt, accumulatedText, continuationNumber),
      model: opts.model,
      temperature: opts.temperature,
      maxTokens: AGENT_TASK_SEGMENT_MAX_TOKENS,
      imageAttachments: opts.imageAttachments,
      metadata:
        segment === 0
          ? opts.metadata
          : {
              ...(opts.metadata ?? {}),
              agent_task_continuation: true,
              agent_task_continuation_segment: continuationNumber,
            },
    });

    addUsage(accumulatedUsage, result.usage);
    if (typeof result.costUsd === "number" && Number.isFinite(result.costUsd)) {
      accumulatedCostUsd += result.costUsd;
      hasCost = true;
    }

    const segmentText = typeof result.text === "string" ? result.text : "";
    if (!segmentText.trim()) {
      if (segment === 0) {
        throw new Error(
          `Agent-task ${opts.artifactLabel} initial segment returned no usable text; no result was evaluated or retained`,
        );
      }
      throw new Error(
        `Agent-task ${opts.artifactLabel} continuation ${continuationNumber} returned no new text; no result was evaluated or retained`,
      );
    }

    if (segment === 0) {
      accumulatedText = segmentText;
    } else {
      const combined = appendContinuation(accumulatedText, segmentText);
      const appendedText = combined.slice(accumulatedText.length);
      if (combined.length <= accumulatedText.length || !appendedText.trim()) {
        throw new Error(
          `Agent-task ${opts.artifactLabel} continuation ${continuationNumber} returned no new text; no result was evaluated or retained`,
        );
      }
      accumulatedText = combined;
    }

    if (accumulatedText.length > AGENT_TASK_MAX_ACCUMULATED_CHARACTERS) {
      throw new Error(
        `Agent-task ${opts.artifactLabel} exceeded the bounded accumulated output size; no result was evaluated or retained`,
      );
    }

    if (!outputWasTruncated(result)) {
      return {
        ...result,
        text: accumulatedText,
        usage: accumulatedUsage,
        costUsd: hasCost ? accumulatedCostUsd : result.costUsd,
        metadata: {
          ...(result.metadata ?? {}),
          agentTaskContinuationCount: continuationNumber,
        },
      };
    }

    if (segment === AGENT_TASK_MAX_CONTINUATIONS) {
      throw new Error(
        `Agent-task ${opts.artifactLabel} remained truncated after ${AGENT_TASK_MAX_CONTINUATIONS} continuation attempts; no result was evaluated or retained`,
      );
    }
  }

  throw new Error(
    `Agent-task ${opts.artifactLabel} exhausted its bounded continuation attempts; no result was evaluated or retained`,
  );
}
