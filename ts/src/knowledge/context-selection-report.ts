export interface ContextSelectionCandidateInput {
  artifact_id?: string;
  artifact_type?: string;
  source?: string;
  candidate_token_estimate?: number;
  selected_token_estimate?: number;
  selected?: boolean;
  selection_reason?: string;
  candidate_content_hash?: string;
  selected_content_hash?: string;
  useful?: boolean | null;
  freshness_generation_delta?: number | null;
}

export interface ContextSelectionDecisionInput {
  run_id?: string;
  scenario_name?: string;
  generation?: number;
  stage?: string;
  created_at?: string;
  candidates?: ContextSelectionCandidateInput[];
  metadata?: Record<string, unknown>;
}

export interface ContextSelectionDiagnosticPolicy {
  duplicate_content_rate_threshold: number;
  useful_artifact_recall_floor: number;
  selected_token_estimate_threshold: number;
  compaction_cache_hit_rate_floor: number;
  compaction_cache_min_lookups: number;
}

export interface ContextSelectionDiagnostic {
  code: string;
  severity: "info" | "warning";
  metric_name: string;
  value: number;
  threshold: number;
  message: string;
  recommendation: string;
  generation: number;
  stage: string;
}

export interface ContextSelectionTelemetryCard {
  key: string;
  label: string;
  value: string;
  severity: "ok" | "info" | "warning";
  detail: string;
}

export interface ContextSelectionStageSummary {
  run_id: string;
  scenario_name: string;
  generation: number;
  stage: string;
  created_at: string;
  candidate_count: number;
  selected_count: number;
  candidate_token_estimate: number;
  selected_token_estimate: number;
  selection_rate: number;
  duplicate_content_rate: number;
  useful_artifact_recall: number | null;
  mean_selected_freshness_generation_delta: number | null;
  budget_input_token_estimate: number;
  budget_output_token_estimate: number;
  budget_token_reduction: number;
  budget_dedupe_hit_count: number;
  budget_component_cap_hit_count: number;
  budget_trimmed_component_count: number;
  compaction_cache_hits: number;
  compaction_cache_misses: number;
  compaction_cache_lookups: number;
  compaction_cache_hit_rate: number | null;
}

export interface ContextSelectionReportPayload {
  status: "completed";
  run_id: string;
  scenario_name: string;
  decision_count: number;
  generation_count: number;
  summary: ContextSelectionReportSummary;
  telemetry_cards: ContextSelectionTelemetryCard[];
  diagnostic_count: number;
  diagnostics: ContextSelectionDiagnostic[];
  stages: ContextSelectionStageSummary[];
}

export interface ContextSelectionReportSummary {
  candidate_count: number;
  selected_count: number;
  candidate_token_estimate: number;
  selected_token_estimate: number;
  selection_rate: number;
  mean_selection_rate: number;
  mean_duplicate_content_rate: number;
  mean_selected_token_estimate: number;
  max_selected_token_estimate: number;
  mean_useful_artifact_recall: number | null;
  mean_selected_freshness_generation_delta: number | null;
  budget_input_token_estimate: number;
  budget_output_token_estimate: number;
  budget_token_reduction: number;
  budget_dedupe_hit_count: number;
  budget_component_cap_hit_count: number;
  budget_trimmed_component_count: number;
  compaction_cache_hits: number;
  compaction_cache_misses: number;
  compaction_cache_lookups: number;
  compaction_cache_hit_rate: number | null;
}

const DEFAULT_POLICY: ContextSelectionDiagnosticPolicy = {
  duplicate_content_rate_threshold: 0.25,
  useful_artifact_recall_floor: 0.70,
  selected_token_estimate_threshold: 8000,
  compaction_cache_hit_rate_floor: 0.50,
  compaction_cache_min_lookups: 5,
};

export class ContextSelectionReport {
  readonly runId: string;
  readonly scenarioName: string;
  readonly stages: ContextSelectionStageSummary[];

  constructor(runId: string, scenarioName: string, stages: ContextSelectionStageSummary[]) {
    this.runId = runId;
    this.scenarioName = scenarioName;
    this.stages = stages;
  }

  summary(): ContextSelectionReportSummary {
    const candidateCount = sum(this.stages.map((stage) => stage.candidate_count));
    const selectedCount = sum(this.stages.map((stage) => stage.selected_count));
    const candidateTokens = sum(this.stages.map((stage) => stage.candidate_token_estimate));
    const selectedTokens = sum(this.stages.map((stage) => stage.selected_token_estimate));
    const compactionCacheHits = sum(this.stages.map((stage) => stage.compaction_cache_hits));
    const compactionCacheLookups = sum(this.stages.map((stage) => stage.compaction_cache_lookups));
    return {
      candidate_count: candidateCount,
      selected_count: selectedCount,
      candidate_token_estimate: candidateTokens,
      selected_token_estimate: selectedTokens,
      selection_rate: candidateCount ? selectedCount / candidateCount : 0,
      mean_selection_rate: mean(this.stages.map((stage) => stage.selection_rate)),
      mean_duplicate_content_rate: mean(this.stages.map((stage) => stage.duplicate_content_rate)),
      mean_selected_token_estimate: this.stages.length ? selectedTokens / this.stages.length : 0,
      max_selected_token_estimate: Math.max(0, ...this.stages.map((stage) => stage.selected_token_estimate)),
      mean_useful_artifact_recall: meanOptional(this.stages.map((stage) => stage.useful_artifact_recall)),
      mean_selected_freshness_generation_delta: meanOptional(
        this.stages.map((stage) => stage.mean_selected_freshness_generation_delta),
      ),
      budget_input_token_estimate: sum(this.stages.map((stage) => stage.budget_input_token_estimate)),
      budget_output_token_estimate: sum(this.stages.map((stage) => stage.budget_output_token_estimate)),
      budget_token_reduction: sum(this.stages.map((stage) => stage.budget_token_reduction)),
      budget_dedupe_hit_count: sum(this.stages.map((stage) => stage.budget_dedupe_hit_count)),
      budget_component_cap_hit_count: sum(this.stages.map((stage) => stage.budget_component_cap_hit_count)),
      budget_trimmed_component_count: sum(this.stages.map((stage) => stage.budget_trimmed_component_count)),
      compaction_cache_hits: compactionCacheHits,
      compaction_cache_misses: sum(this.stages.map((stage) => stage.compaction_cache_misses)),
      compaction_cache_lookups: compactionCacheLookups,
      compaction_cache_hit_rate: compactionCacheLookups ? compactionCacheHits / compactionCacheLookups : null,
    };
  }

  diagnostics(policy: ContextSelectionDiagnosticPolicy = DEFAULT_POLICY): ContextSelectionDiagnostic[] {
    if (this.stages.length === 0) return [];

    const diagnostics: ContextSelectionDiagnostic[] = [];
    const duplicateStage = maxBy(this.stages, (stage) => stage.duplicate_content_rate);
    if (duplicateStage.duplicate_content_rate >= policy.duplicate_content_rate_threshold) {
      diagnostics.push({
        code: "HIGH_DUPLICATE_CONTENT_RATE",
        severity: "warning",
        metric_name: "duplicate_content_rate",
        value: duplicateStage.duplicate_content_rate,
        threshold: policy.duplicate_content_rate_threshold,
        message: "Selected context contains repeated content in a single prompt assembly stage.",
        recommendation: "Deduplicate equivalent prompt components before selection and keep one canonical source.",
        generation: duplicateStage.generation,
        stage: duplicateStage.stage,
      });
    }

    const usefulStages = this.stages.filter((stage) => stage.useful_artifact_recall !== null);
    if (usefulStages.length > 0) {
      const recallStage = minBy(usefulStages, (stage) => stage.useful_artifact_recall ?? 0);
      const recall = recallStage.useful_artifact_recall;
      if (recall !== null && recall < policy.useful_artifact_recall_floor) {
        diagnostics.push({
          code: "LOW_USEFUL_ARTIFACT_RECALL",
          severity: "warning",
          metric_name: "useful_artifact_recall",
          value: recall,
          threshold: policy.useful_artifact_recall_floor,
          message: "Useful artifacts were available but omitted from selected context.",
          recommendation: "Promote useful artifacts earlier in context ranking or lower-priority noisy components.",
          generation: recallStage.generation,
          stage: recallStage.stage,
        });
      }
    }

    const tokenStage = maxBy(this.stages, (stage) => stage.selected_token_estimate);
    if (tokenStage.selected_token_estimate > policy.selected_token_estimate_threshold) {
      diagnostics.push({
        code: "SELECTED_TOKEN_BLOAT",
        severity: "warning",
        metric_name: "selected_token_estimate",
        value: tokenStage.selected_token_estimate,
        threshold: policy.selected_token_estimate_threshold,
        message: "One prompt assembly stage selected an unusually large context payload.",
        recommendation: "Reduce selected context by tightening budget filters and summarizing bulky artifacts.",
        generation: tokenStage.generation,
        stage: tokenStage.stage,
      });
    }

    const cacheStages = this.stages.filter(
      (stage) =>
        stage.compaction_cache_hit_rate !== null &&
        stage.compaction_cache_lookups >= policy.compaction_cache_min_lookups,
    );
    if (cacheStages.length > 0) {
      const cacheStage = minBy(cacheStages, (stage) => stage.compaction_cache_hit_rate ?? 0);
      const hitRate = cacheStage.compaction_cache_hit_rate;
      if (hitRate !== null && hitRate < policy.compaction_cache_hit_rate_floor) {
        diagnostics.push({
          code: "LOW_COMPACTION_CACHE_HIT_RATE",
          severity: "info",
          metric_name: "compaction_cache_hit_rate",
          value: hitRate,
          threshold: policy.compaction_cache_hit_rate_floor,
          message: "Semantic compaction cache reuse was low for a prompt assembly stage.",
          recommendation: "Check whether repeated prompt components use stable canonical text before cache lookup.",
          generation: cacheStage.generation,
          stage: cacheStage.stage,
        });
      }
    }
    return diagnostics;
  }

  telemetryCards(policy: ContextSelectionDiagnosticPolicy = DEFAULT_POLICY): ContextSelectionTelemetryCard[] {
    const summary = this.summary();
    const diagnostics = this.diagnostics(policy);
    const diagnosticCodes = new Set(diagnostics.map((diagnostic) => diagnostic.code));
    return [
      selectedContextCard(summary, diagnosticCodes),
      contextBudgetCard(summary),
      semanticCompactionCacheCard(summary, diagnosticCodes),
      diagnosticsCard(diagnostics),
    ];
  }

  toDict(): ContextSelectionReportPayload {
    const generations = new Set(this.stages.map((stage) => stage.generation));
    const diagnostics = this.diagnostics();
    return {
      status: "completed",
      run_id: this.runId,
      scenario_name: this.scenarioName,
      decision_count: this.stages.length,
      generation_count: generations.size,
      summary: this.summary(),
      telemetry_cards: this.telemetryCards(),
      diagnostic_count: diagnostics.length,
      diagnostics,
      stages: this.stages,
    };
  }

  toMarkdown(): string {
    const summary = this.summary();
    const lines = [
      `# Context Selection Report: ${this.runId}`,
      "",
      `- Scenario: ${this.scenarioName}`,
      `- Decisions: ${this.stages.length}`,
      `- Selected tokens: ${summary.selected_token_estimate}`,
      `- Selection rate: ${formatPercent(summary.selection_rate, 2)}`,
      `- Mean duplicate content rate: ${formatPercent(summary.mean_duplicate_content_rate, 2)}`,
    ];
    if (summary.mean_selected_freshness_generation_delta !== null) {
      lines.push(
        `- Mean selected freshness delta: ${summary.mean_selected_freshness_generation_delta.toFixed(2)} generation(s)`,
      );
    }
    lines.push(
      "",
      "## Context Budget",
      `- Input estimate: ${summary.budget_input_token_estimate}`,
      `- Output estimate: ${summary.budget_output_token_estimate}`,
      `- Token reduction: ${summary.budget_token_reduction}`,
      `- Dedupe hits: ${summary.budget_dedupe_hit_count}`,
      `- Component caps: ${summary.budget_component_cap_hit_count}`,
      `- Global trims: ${summary.budget_trimmed_component_count}`,
      "",
      "## Semantic Compaction Cache",
      `- Hit rate: ${formatOptionalPercent(summary.compaction_cache_hit_rate)}`,
      `- Hits: ${summary.compaction_cache_hits}`,
      `- Misses: ${summary.compaction_cache_misses}`,
      `- Lookups: ${summary.compaction_cache_lookups}`,
    );
    const diagnostics = this.diagnostics();
    if (diagnostics.length > 0) {
      lines.push("", "## Diagnostics");
      for (const diagnostic of diagnostics) {
        lines.push(`- ${diagnostic.code}: ${diagnostic.recommendation}`);
      }
    }
    return lines.join("\n");
  }
}

export function buildContextSelectionReport(decisions: ContextSelectionDecisionInput[]): ContextSelectionReport {
  const stages = [...decisions]
    .sort((a, b) => coerceNumber(a.generation) - coerceNumber(b.generation) || String(a.stage ?? "").localeCompare(String(b.stage ?? "")))
    .map(stageSummaryFromDecision);
  const runIds = new Set(stages.map((stage) => stage.run_id).filter(Boolean));
  const scenarioNames = new Set(stages.map((stage) => stage.scenario_name).filter(Boolean));
  if (runIds.size > 1) throw new Error("context selection report requires a single run_id");
  if (scenarioNames.size > 1) throw new Error("context selection report requires a single scenario_name");
  return new ContextSelectionReport([...runIds][0] ?? "", [...scenarioNames][0] ?? "", stages);
}

function stageSummaryFromDecision(decision: ContextSelectionDecisionInput): ContextSelectionStageSummary {
  const metrics = decisionMetrics(decision);
  return {
    run_id: String(decision.run_id ?? ""),
    scenario_name: String(decision.scenario_name ?? ""),
    generation: coerceNumber(decision.generation),
    stage: String(decision.stage ?? ""),
    created_at: String(decision.created_at ?? ""),
    candidate_count: intMetric(metrics, "candidate_count"),
    selected_count: intMetric(metrics, "selected_count"),
    candidate_token_estimate: intMetric(metrics, "candidate_token_estimate"),
    selected_token_estimate: intMetric(metrics, "selected_token_estimate"),
    selection_rate: floatMetric(metrics, "selection_rate"),
    duplicate_content_rate: floatMetric(metrics, "duplicate_content_rate"),
    useful_artifact_recall: optionalFloatMetric(metrics, "useful_artifact_recall"),
    mean_selected_freshness_generation_delta: optionalFloatMetric(
      metrics,
      "mean_selected_freshness_generation_delta",
    ),
    budget_input_token_estimate: intMetric(metrics, "budget_input_token_estimate"),
    budget_output_token_estimate: intMetric(metrics, "budget_output_token_estimate"),
    budget_token_reduction: intMetric(metrics, "budget_token_reduction"),
    budget_dedupe_hit_count: intMetric(metrics, "budget_dedupe_hit_count"),
    budget_component_cap_hit_count: intMetric(metrics, "budget_component_cap_hit_count"),
    budget_trimmed_component_count: intMetric(metrics, "budget_trimmed_component_count"),
    compaction_cache_hits: intMetric(metrics, "compaction_cache_hits"),
    compaction_cache_misses: intMetric(metrics, "compaction_cache_misses"),
    compaction_cache_lookups: intMetric(metrics, "compaction_cache_lookups"),
    compaction_cache_hit_rate: optionalFloatMetric(metrics, "compaction_cache_hit_rate"),
  };
}

function decisionMetrics(decision: ContextSelectionDecisionInput): Record<string, number | null> {
  const candidates = decision.candidates ?? [];
  const selected = candidates.filter((candidate) => candidate.selected === true);
  const usefulCandidates = candidates.filter((candidate) => candidate.useful === true);
  const usefulSelected = selected.filter((candidate) => candidate.useful === true);
  const freshness = selected
    .map((candidate) => candidate.freshness_generation_delta)
    .filter((value): value is number => typeof value === "number");
  const duplicateCount = duplicateSelectedHashCount(selected);
  const budgetTelemetry = coerceRecord(decision.metadata?.context_budget_telemetry);
  const budgetInputTokens = coerceNumber(budgetTelemetry.input_token_estimate);
  const budgetOutputTokens = coerceNumber(budgetTelemetry.output_token_estimate);
  const compactionCache = coerceRecord(decision.metadata?.prompt_compaction_cache);
  const compactionHits = coerceNumber(compactionCache.hits);
  const compactionMisses = coerceNumber(compactionCache.misses);
  const rawLookups = coerceNumber(compactionCache.lookups);
  const compactionLookups = rawLookups || compactionHits + compactionMisses;
  return {
    candidate_count: candidates.length,
    selected_count: selected.length,
    candidate_token_estimate: sum(candidates.map((candidate) => coerceNumber(candidate.candidate_token_estimate))),
    selected_token_estimate: sum(selected.map((candidate) => coerceNumber(candidate.selected_token_estimate))),
    selection_rate: candidates.length ? selected.length / candidates.length : 0,
    duplicate_content_rate: selected.length ? duplicateCount / selected.length : 0,
    useful_candidate_count: usefulCandidates.length,
    useful_selected_count: usefulSelected.length,
    useful_artifact_recall: usefulCandidates.length ? usefulSelected.length / usefulCandidates.length : null,
    mean_selected_freshness_generation_delta: freshness.length ? sum(freshness) / freshness.length : null,
    budget_input_token_estimate: budgetInputTokens,
    budget_output_token_estimate: budgetOutputTokens,
    budget_token_reduction: Math.max(0, budgetInputTokens - budgetOutputTokens),
    budget_dedupe_hit_count: coerceNumber(budgetTelemetry.dedupe_hit_count),
    budget_component_cap_hit_count: coerceNumber(budgetTelemetry.component_cap_hit_count),
    budget_trimmed_component_count: coerceNumber(budgetTelemetry.trimmed_component_count),
    compaction_cache_hits: compactionHits,
    compaction_cache_misses: compactionMisses,
    compaction_cache_lookups: compactionLookups,
    compaction_cache_hit_rate: compactionLookups ? compactionHits / compactionLookups : null,
  };
}

function selectedContextCard(
  summary: ContextSelectionReportSummary,
  diagnosticCodes: Set<string>,
): ContextSelectionTelemetryCard {
  return {
    key: "selected_context",
    label: "Selected context",
    value: `${summary.selected_token_estimate} est. tokens`,
    severity: diagnosticCodes.has("SELECTED_TOKEN_BLOAT") ? "warning" : "ok",
    detail: `${summary.selected_count}/${summary.candidate_count} components selected (${formatPercent(summary.selection_rate, 1)})`,
  };
}

function contextBudgetCard(summary: ContextSelectionReportSummary): ContextSelectionTelemetryCard {
  if (summary.budget_input_token_estimate <= 0) {
    return {
      key: "context_budget",
      label: "Context budget",
      value: "No telemetry",
      severity: "info",
      detail: "No context budget telemetry recorded.",
    };
  }
  return {
    key: "context_budget",
    label: "Context budget",
    value: `${summary.budget_token_reduction} est. tokens reduced`,
    severity: summary.budget_trimmed_component_count > 0 ? "warning" : "ok",
    detail:
      `${summary.budget_input_token_estimate}->${summary.budget_output_token_estimate} est. tokens; ` +
      `${summary.budget_dedupe_hit_count} dedupe, ${summary.budget_component_cap_hit_count} caps, ` +
      `${summary.budget_trimmed_component_count} trims`,
  };
}

function semanticCompactionCacheCard(
  summary: ContextSelectionReportSummary,
  diagnosticCodes: Set<string>,
): ContextSelectionTelemetryCard {
  if (summary.compaction_cache_lookups <= 0 || summary.compaction_cache_hit_rate === null) {
    return {
      key: "semantic_compaction_cache",
      label: "Semantic compaction cache",
      value: "No lookups",
      severity: "info",
      detail: "No semantic compaction cache lookups recorded.",
    };
  }
  return {
    key: "semantic_compaction_cache",
    label: "Semantic compaction cache",
    value: `${formatPercent(summary.compaction_cache_hit_rate, 1)} hit rate`,
    severity: diagnosticCodes.has("LOW_COMPACTION_CACHE_HIT_RATE") ? "warning" : "ok",
    detail:
      `${summary.compaction_cache_hits} hits, ${summary.compaction_cache_misses} misses, ` +
      `${summary.compaction_cache_lookups} lookups`,
  };
}

function diagnosticsCard(diagnostics: ContextSelectionDiagnostic[]): ContextSelectionTelemetryCard {
  return {
    key: "diagnostics",
    label: "Diagnostics",
    value: `${diagnostics.length} finding(s)`,
    severity: diagnostics.length > 0 ? "warning" : "ok",
    detail: diagnostics.length ? diagnostics.map((diagnostic) => diagnostic.code).join(", ") : "No diagnostics.",
  };
}

function duplicateSelectedHashCount(candidates: ContextSelectionCandidateInput[]): number {
  const counts = new Map<string, number>();
  for (const candidate of candidates) {
    const hash = candidate.selected_content_hash;
    if (!hash) continue;
    counts.set(hash, (counts.get(hash) ?? 0) + 1);
  }
  return [...counts.values()].reduce((total, count) => total + (count > 1 ? count - 1 : 0), 0);
}

function coerceRecord(value: unknown): Record<string, unknown> {
  if (typeof value === "object" && value !== null && !Array.isArray(value)) {
    return value as Record<string, unknown>;
  }
  return {};
}

function coerceNumber(value: unknown): number {
  if (typeof value === "number" && Number.isFinite(value)) return Math.trunc(value);
  if (typeof value === "string") {
    const parsed = Number.parseInt(value, 10);
    return Number.isFinite(parsed) ? parsed : 0;
  }
  return 0;
}

function intMetric(metrics: Record<string, number | null>, key: string): number {
  return coerceNumber(metrics[key]);
}

function floatMetric(metrics: Record<string, number | null>, key: string): number {
  const value = metrics[key];
  return typeof value === "number" && Number.isFinite(value) ? value : 0;
}

function optionalFloatMetric(metrics: Record<string, number | null>, key: string): number | null {
  const value = metrics[key];
  return typeof value === "number" && Number.isFinite(value) ? value : null;
}

function sum(values: number[]): number {
  return values.reduce((total, value) => total + value, 0);
}

function mean(values: number[]): number {
  return values.length ? sum(values) / values.length : 0;
}

function meanOptional(values: Array<number | null>): number | null {
  const items = values.filter((value): value is number => value !== null);
  return items.length ? sum(items) / items.length : null;
}

function maxBy<T>(items: T[], score: (item: T) => number): T {
  return items.reduce((best, item) => (score(item) > score(best) ? item : best), items[0]!);
}

function minBy<T>(items: T[], score: (item: T) => number): T {
  return items.reduce((best, item) => (score(item) < score(best) ? item : best), items[0]!);
}

function formatPercent(value: number, digits: number): string {
  return `${(value * 100).toFixed(digits)}%`;
}

function formatOptionalPercent(value: number | null): string {
  return value === null ? "n/a" : formatPercent(value, 1);
}
