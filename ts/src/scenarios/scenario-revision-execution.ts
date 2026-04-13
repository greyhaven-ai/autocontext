import type { LLMProvider } from "../types/index.js";
import { normalizeScenarioRevisionSpec } from "./revision-spec-normalizer.js";

export interface ExecuteScenarioRevisionOpts {
  currentSpec: Record<string, unknown>;
  family: string;
  prompt: string;
  provider: LLMProvider;
  model?: string;
}

export interface ExecutedScenarioRevisionResult {
  original: Record<string, unknown>;
  revised: Record<string, unknown>;
  changesApplied: boolean;
  error?: string;
}

export function parseJsonFromLLMResponse(text: string): Record<string, unknown> | null {
  const trimmed = text.trim();

  try {
    return JSON.parse(trimmed);
  } catch {
    // continue
  }

  const jsonStart = trimmed.indexOf("{");
  const jsonEnd = trimmed.lastIndexOf("}");
  if (jsonStart !== -1 && jsonEnd > jsonStart) {
    try {
      return JSON.parse(trimmed.slice(jsonStart, jsonEnd + 1));
    } catch {
      // continue
    }
  }

  return null;
}

export async function executeScenarioRevision(
  opts: ExecuteScenarioRevisionOpts,
): Promise<ExecutedScenarioRevisionResult> {
  const { currentSpec, family, prompt, provider, model } = opts;
  const original = { ...currentSpec };

  try {
    const result = await provider.complete({
      systemPrompt: `You are a scenario designer. Revise the ${family} spec based on user feedback. Output only valid JSON.`,
      userPrompt: prompt,
      ...(model ? { model } : {}),
    });

    const revised = parseJsonFromLLMResponse(result.text);
    if (!revised) {
      return {
        original,
        revised: original,
        changesApplied: false,
        error: "LLM response was not valid JSON",
      };
    }

    const merged = { ...original, ...revised };
    const normalized = normalizeScenarioRevisionSpec(family, merged);

    return {
      original,
      revised: normalized,
      changesApplied: true,
    };
  } catch (err) {
    return {
      original,
      revised: original,
      changesApplied: false,
      error: err instanceof Error ? err.message : String(err),
    };
  }
}
