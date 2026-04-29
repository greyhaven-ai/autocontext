import type { CompletionResult, LLMProvider } from "../types/index.js";
import { HookEvents, type HookBus } from "./hooks.js";

export interface HookedProviderCompletionOpts {
  hookBus?: HookBus | null;
  provider: LLMProvider;
  role: string;
  systemPrompt: string;
  userPrompt: string;
  model?: string;
  temperature?: number;
  maxTokens?: number;
  metadata?: Record<string, unknown>;
}

export async function completeWithProviderHooks(
  opts: HookedProviderCompletionOpts,
): Promise<CompletionResult> {
  const request = {
    provider: opts.provider.name,
    role: opts.role,
    model: opts.model,
    systemPrompt: opts.systemPrompt,
    userPrompt: opts.userPrompt,
    temperature: opts.temperature,
    maxTokens: opts.maxTokens,
    ...(opts.metadata ?? {}),
  };
  const before = emitHook(opts.hookBus ?? null, HookEvents.BEFORE_PROVIDER_REQUEST, request);
  const finalSystemPrompt = readString(before.payload.systemPrompt) ?? opts.systemPrompt;
  const finalUserPrompt = readString(before.payload.userPrompt) ?? opts.userPrompt;
  const finalModel = readString(before.payload.model) ?? opts.model;
  const finalTemperature = readNumber(before.payload.temperature) ?? opts.temperature;
  const finalMaxTokens = readNumber(before.payload.maxTokens) ?? opts.maxTokens;

  const result = await opts.provider.complete({
    systemPrompt: finalSystemPrompt,
    userPrompt: finalUserPrompt,
    model: finalModel,
    temperature: finalTemperature,
    maxTokens: finalMaxTokens,
  });
  const after = emitHook(opts.hookBus ?? null, HookEvents.AFTER_PROVIDER_RESPONSE, {
    provider: opts.provider.name,
    role: opts.role,
    model: finalModel,
    request: {
      ...request,
      systemPrompt: finalSystemPrompt,
      userPrompt: finalUserPrompt,
      model: finalModel,
      temperature: finalTemperature,
      maxTokens: finalMaxTokens,
    },
    text: result.text,
    usage: result.usage,
    costUsd: result.costUsd,
    ...(opts.metadata ?? {}),
  });

  return {
    ...result,
    text: readString(after.payload.text) ?? result.text,
    model: readString(after.payload.model) ?? result.model,
    usage: readNumberRecord(after.payload.usage) ?? result.usage,
    costUsd: readNumber(after.payload.costUsd) ?? result.costUsd,
  };
}

function emitHook(
  hookBus: HookBus | null,
  name: HookEvents,
  payload: Record<string, unknown>,
): { payload: Record<string, unknown> } {
  if (!hookBus?.hasHandlers(name)) {
    return { payload };
  }
  const event = hookBus.emit(name, payload);
  event.raiseIfBlocked();
  return event;
}

function readString(value: unknown): string | undefined {
  return typeof value === "string" ? value : undefined;
}

function readNumber(value: unknown): number | undefined {
  return typeof value === "number" && Number.isFinite(value) ? value : undefined;
}

function readNumberRecord(value: unknown): Record<string, number> | undefined {
  if (!isRecord(value)) {
    return undefined;
  }
  const result: Record<string, number> = {};
  for (const [key, raw] of Object.entries(value)) {
    if (typeof raw === "number" && Number.isFinite(raw)) {
      result[key] = raw;
    }
  }
  return result;
}

function isRecord(value: unknown): value is Record<string, unknown> {
  return typeof value === "object" && value !== null && !Array.isArray(value);
}
