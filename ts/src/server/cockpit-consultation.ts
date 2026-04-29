import { existsSync, mkdirSync, readFileSync, writeFileSync } from "node:fs";
import { dirname, isAbsolute, relative, resolve } from "node:path";
import type { AppSettings } from "../config/index.js";
import type { CreateProviderOpts } from "../providers/provider-factory.js";
import type { GenerationRow, SQLiteStore } from "../storage/index.js";
import type { CompletionResult, LLMProvider } from "../types/index.js";
import type { CockpitApiResponse } from "./cockpit-api.js";

export async function requestConsultation(
  store: SQLiteStore,
  opts: {
    body: Record<string, unknown>;
    createProvider: (opts: CreateProviderOpts) => LLMProvider;
    runId: string;
    runsRoot: string;
    settings: AppSettings;
  },
): Promise<CockpitApiResponse> {
  if (!opts.settings.consultationEnabled) {
    return { status: 400, body: { detail: "Consultation is not enabled" } };
  }

  const run = store.getRun(opts.runId);
  if (!run) {
    return { status: 404, body: { detail: `Run '${opts.runId}' not found` } };
  }

  const generations = store.getGenerations(opts.runId);
  if (generations.length === 0) {
    return {
      status: 400,
      body: { detail: "Cannot request consultation for a run with no generations yet" },
    };
  }

  const generationResult = resolveConsultationGeneration(opts.body, generations);
  if ("response" in generationResult) {
    return generationResult.response;
  }

  if (opts.settings.consultationCostBudget > 0) {
    const spent = store.getTotalConsultationCost(opts.runId);
    if (spent >= opts.settings.consultationCostBudget) {
      return {
        status: 429,
        body: {
          detail: `Consultation budget exceeded (spent $${spent.toFixed(2)} `
            + `of $${opts.settings.consultationCostBudget.toFixed(2)})`,
        },
      };
    }
  }

  const providerResult = createConsultationProvider(opts.settings, opts.createProvider);
  if ("response" in providerResult) {
    return providerResult.response;
  }

  const contextSummary = readOptionalString(opts.body.context_summary)
    ?? readOptionalString(opts.body.contextSummary)
    ?? `Operator-requested consultation for run ${opts.runId} at generation ${generationResult.generation}`;
  const strategySummary = latestCompetitorStrategy(store, opts.runId, generations);
  const completionResult = await completeConsultation(providerResult.provider, opts.settings, {
    contextSummary,
    gateHistory: generations.map((generation) => generation.gate_decision),
    generation: generationResult.generation,
    runId: opts.runId,
    scoreHistory: generations.map((generation) => generation.best_score),
    strategySummary,
  });
  if ("response" in completionResult) {
    return completionResult.response;
  }

  const parsed = parseConsultationCompletion(completionResult.completion, providerResult.provider);
  const rowId = store.insertConsultation({
    runId: opts.runId,
    generationIndex: generationResult.generation,
    trigger: "operator_request",
    contextSummary,
    critique: parsed.critique,
    alternativeHypothesis: parsed.alternativeHypothesis,
    tiebreakRecommendation: parsed.tiebreakRecommendation,
    suggestedNextAction: parsed.suggestedNextAction,
    rawResponse: parsed.rawResponse,
    modelUsed: parsed.modelUsed,
    costUsd: parsed.costUsd,
  });
  const advisoryMarkdown = renderConsultationAdvisory(parsed);
  writeConsultationAdvisory(opts.runsRoot, opts.runId, generationResult.generation, advisoryMarkdown);

  return {
    status: 200,
    body: {
      consultation_id: rowId,
      run_id: opts.runId,
      generation: generationResult.generation,
      trigger: "operator_request",
      critique: parsed.critique,
      alternative_hypothesis: parsed.alternativeHypothesis,
      tiebreak_recommendation: parsed.tiebreakRecommendation,
      suggested_next_action: parsed.suggestedNextAction,
      model_used: parsed.modelUsed,
      cost_usd: parsed.costUsd,
      advisory_markdown: advisoryMarkdown,
    },
  };
}

function resolveConsultationGeneration(
  body: Record<string, unknown>,
  generations: GenerationRow[],
): { generation: number } | { response: CockpitApiResponse } {
  const requested = body.generation;
  if (requested !== undefined && requested !== null) {
    if (typeof requested !== "number" || !Number.isInteger(requested) || requested < 1) {
      return { response: { status: 400, body: { detail: "generation must be a positive integer" } } };
    }
    if (!generations.some((generation) => generation.generation_index === requested)) {
      return {
        response: {
          status: 404,
          body: { detail: `Generation ${requested} not found` },
        },
      };
    }
    return { generation: requested };
  }
  return {
    generation: Math.max(...generations.map((generation) => generation.generation_index)),
  };
}

function createConsultationProvider(
  settings: AppSettings,
  createProvider: (opts: CreateProviderOpts) => LLMProvider,
): { provider: LLMProvider } | { response: CockpitApiResponse } {
  const providerType = settings.consultationProvider.trim() || "anthropic";
  const apiKey = settings.consultationApiKey || settings.anthropicApiKey || "";
  if (requiresConsultationApiKey(providerType) && apiKey.length === 0) {
    return {
      response: {
        status: 503,
        body: { detail: "Consultation provider not configured (missing API key)" },
      },
    };
  }

  try {
    return {
      provider: createProvider({
        apiKey,
        baseUrl: settings.consultationBaseUrl || undefined,
        claudeFallbackModel: settings.claudeFallbackModel,
        claudeModel: settings.claudeModel,
        claudePermissionMode: settings.claudePermissionMode,
        claudeSessionPersistence: settings.claudeSessionPersistence,
        claudeTimeout: settings.claudeTimeout,
        claudeTools: settings.claudeTools ?? undefined,
        codexApprovalMode: settings.codexApprovalMode,
        codexModel: settings.codexModel,
        codexQuiet: settings.codexQuiet,
        codexTimeout: settings.codexTimeout,
        codexWorkspace: settings.codexWorkspace,
        model: settings.consultationModel,
        piCommand: settings.piCommand,
        piModel: settings.piModel,
        piNoContextFiles: settings.piNoContextFiles,
        piRpcApiKey: settings.piRpcApiKey,
        piRpcEndpoint: settings.piRpcEndpoint,
        piRpcPersistent: settings.piRpcPersistent,
        piRpcSessionPersistence: settings.piRpcSessionPersistence,
        piTimeout: settings.piTimeout,
        piWorkspace: settings.piWorkspace,
        providerType,
      }),
    };
  } catch (error: unknown) {
    return {
      response: {
        status: 503,
        body: { detail: `Consultation provider not configured: ${errorMessage(error)}` },
      },
    };
  }
}

function requiresConsultationApiKey(providerType: string): boolean {
  return new Set([
    "anthropic",
    "azure-openai",
    "gemini",
    "groq",
    "mistral",
    "openai",
    "openai-compatible",
    "openrouter",
  ]).has(providerType.toLowerCase().trim());
}

async function completeConsultation(
  provider: LLMProvider,
  settings: AppSettings,
  opts: {
    contextSummary: string;
    gateHistory: string[];
    generation: number;
    runId: string;
    scoreHistory: number[];
    strategySummary: string;
  },
): Promise<{ completion: CompletionResult } | { response: CockpitApiResponse }> {
  try {
    const completion = await provider.complete({
      systemPrompt: [
        "You are a strategy consultant for an iterative optimisation system.",
        "Provide analysis using these markdown sections:",
        "## Critique",
        "## Alternative Hypothesis",
        "## Tiebreak Recommendation",
        "## Suggested Next Action",
      ].join("\n"),
      userPrompt: [
        `Run: ${opts.runId}, Generation: ${opts.generation}`,
        "Trigger: operator_request",
        `Context: ${opts.contextSummary}`,
        `Current strategy: ${opts.strategySummary}`,
        `Score history: ${formatNumberHistory(opts.scoreHistory)}`,
        `Gate history: ${opts.gateHistory.join(" -> ")}`,
      ].join("\n"),
      model: settings.consultationModel,
      temperature: 0.3,
      maxTokens: 1200,
    });
    return { completion };
  } catch (error: unknown) {
    return {
      response: {
        status: 502,
        body: { detail: `Consultation call failed: ${errorMessage(error)}` },
      },
    };
  } finally {
    provider.close?.();
  }
}

interface ParsedConsultation {
  critique: string;
  alternativeHypothesis: string;
  tiebreakRecommendation: string;
  suggestedNextAction: string;
  rawResponse: string;
  modelUsed: string;
  costUsd: number | null;
}

function parseConsultationCompletion(
  completion: CompletionResult,
  provider: LLMProvider,
): ParsedConsultation {
  const critique = extractMarkdownSection(completion.text, "Critique");
  const alternativeHypothesis = extractMarkdownSection(completion.text, "Alternative Hypothesis");
  const tiebreakRecommendation = extractMarkdownSection(completion.text, "Tiebreak Recommendation");
  const suggestedNextAction = extractMarkdownSection(completion.text, "Suggested Next Action");
  const hasStructuredSections = [
    critique,
    alternativeHypothesis,
    tiebreakRecommendation,
    suggestedNextAction,
  ].some((value) => value.length > 0);
  return {
    critique: hasStructuredSections ? critique : completion.text.trim(),
    alternativeHypothesis,
    tiebreakRecommendation,
    suggestedNextAction,
    rawResponse: completion.text,
    modelUsed: completion.model ?? provider.defaultModel(),
    costUsd: completion.costUsd ?? null,
  };
}

function renderConsultationAdvisory(result: ParsedConsultation): string {
  const sections: string[] = [];
  if (result.critique) {
    sections.push(`## Critique\n${result.critique}`);
  }
  if (result.alternativeHypothesis) {
    sections.push(`## Alternative Hypothesis\n${result.alternativeHypothesis}`);
  }
  if (result.tiebreakRecommendation) {
    sections.push(`## Tiebreak Recommendation\n${result.tiebreakRecommendation}`);
  }
  if (result.suggestedNextAction) {
    sections.push(`## Suggested Next Action\n${result.suggestedNextAction}`);
  }
  if (result.modelUsed) {
    sections.push(`---\n*Consultation model: ${result.modelUsed}*`);
  }
  return sections.length > 0 ? sections.join("\n\n") : "*No advisory content.*";
}

function latestCompetitorStrategy(
  store: SQLiteStore,
  runId: string,
  generations: GenerationRow[],
): string {
  for (const generation of [...generations].sort(
    (left, right) => right.generation_index - left.generation_index,
  )) {
    const output = store
      .getAgentOutputs(runId, generation.generation_index)
      .filter((entry) => entry.role === "competitor")
      .at(-1);
    if (output) {
      return truncate(output.content, 500);
    }
  }
  return "";
}

function writeConsultationAdvisory(
  runsRoot: string,
  runId: string,
  generation: number,
  markdown: string,
): void {
  const advisoryPath = resolveContainedPath(
    runsRoot,
    runId,
    "generations",
    `gen_${generation}`,
    "consultation.md",
  );
  mkdirSync(dirname(advisoryPath), { recursive: true });
  if (existsSync(advisoryPath)) {
    const existing = readFileSync(advisoryPath, "utf-8");
    writeFileSync(
      advisoryPath,
      `${existing.trimEnd()}\n\n# Operator Requested Consultation\n\n${markdown}\n`,
      "utf-8",
    );
    return;
  }
  writeFileSync(advisoryPath, `${markdown}\n`, "utf-8");
}

function readOptionalString(value: unknown): string | null {
  return typeof value === "string" && value.trim().length > 0 ? value : null;
}

function formatNumberHistory(values: number[], maxItems = 8): string {
  const recent = values.slice(-maxItems).map((value) => value.toFixed(2)).join(" -> ");
  if (values.length <= maxItems) {
    return recent;
  }
  return `${recent} (recent ${Math.min(values.length, maxItems)} of ${values.length})`;
}

function extractMarkdownSection(content: string, heading: string): string {
  const match = new RegExp(
    `##\\s*${escapeRegExp(heading)}\\s*\\n([\\s\\S]*?)(?=\\n##\\s|$)`,
    "i",
  ).exec(content);
  return match?.[1]?.trim() ?? "";
}

function truncate(value: string, maxLength: number): string {
  return value.length > maxLength ? `${value.slice(0, maxLength)}...` : value;
}

function resolveContainedPath(root: string, ...segments: string[]): string {
  const resolvedRoot = resolve(root);
  const target = resolve(resolvedRoot, ...segments);
  const pathToTarget = relative(resolvedRoot, target);
  if (pathToTarget === "" || (!pathToTarget.startsWith("..") && !isAbsolute(pathToTarget))) {
    return target;
  }
  throw new Error("path escapes configured root");
}

function escapeRegExp(value: string): string {
  return value.replace(/[.*+?^${}()|[\]\\]/g, "\\$&");
}

function errorMessage(error: unknown): string {
  return error instanceof Error ? error.message : String(error);
}
