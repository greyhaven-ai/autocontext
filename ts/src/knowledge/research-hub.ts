import { randomUUID } from "node:crypto";
import { existsSync, mkdirSync, readFileSync, writeFileSync } from "node:fs";
import { dirname, join, relative } from "node:path";

import { detectFamily } from "../scenarios/family-interfaces.js";
import { SCENARIO_REGISTRY } from "../scenarios/registry.js";
import type {
  GenerationRow,
  HubPackageRecordRow,
  HubPromotionRecordRow,
  HubResultRecordRow,
  HubSessionRow,
  NotebookRow,
  SQLiteStore,
} from "../storage/index.js";
import { ArtifactStore } from "./artifact-store.js";
import {
  exportStrategyPackage,
  importStrategyPackage,
  type ConflictPolicy,
} from "./package.js";

export interface ResearchHubServiceOpts {
  runsRoot: string;
  knowledgeRoot: string;
  skillsRoot: string;
  openStore: () => SQLiteStore;
}

export class ResearchHubError extends Error {
  readonly status: number;

  constructor(message: string, status = 400) {
    super(message);
    this.name = "ResearchHubError";
    this.status = status;
  }
}

const SAFE_HUB_ID = /^[A-Za-z0-9][A-Za-z0-9._-]*$/;
const CONFLICT_POLICIES = new Set<ConflictPolicy>(["overwrite", "merge", "skip"]);

interface HubRunEvidence {
  normalizedProgress: string;
  costSummary: string;
  weaknessSummary: string;
  consultationSummary: string;
  frictionSignals: string[];
  delightSignals: string[];
  linkedArtifacts: string[];
}

export class ResearchHubService {
  readonly #runsRoot: string;
  readonly #knowledgeRoot: string;
  readonly #skillsRoot: string;
  readonly #openStore: () => SQLiteStore;
  readonly #artifacts: ArtifactStore;

  constructor(opts: ResearchHubServiceOpts) {
    this.#runsRoot = opts.runsRoot;
    this.#knowledgeRoot = opts.knowledgeRoot;
    this.#skillsRoot = opts.skillsRoot;
    this.#openStore = opts.openStore;
    this.#artifacts = new ArtifactStore({
      runsRoot: opts.runsRoot,
      knowledgeRoot: opts.knowledgeRoot,
    });
  }

  listSessions(): Record<string, unknown>[] {
    return this.#withStore((store) => {
      const metadataBySession = new Map(
        store.listHubSessions().map((session) => [session.session_id, session] as const),
      );
      return store.listNotebooks().map((notebook) => this.#composeSession(
        notebook,
        metadataBySession.get(notebook.session_id) ?? null,
      ));
    });
  }

  getSession(sessionId: string): Record<string, unknown> {
    const safeSessionId = ensureSafeHubId(sessionId);
    return this.#withStore((store) => {
      const session = this.#loadSessionFromStore(store, safeSessionId);
      if (!session) {
        throw new ResearchHubError(`Hub session not found: ${safeSessionId}`, 404);
      }
      return session;
    });
  }

  upsertSession(sessionId: string, body: Record<string, unknown>): Record<string, unknown> {
    const safeSessionId = ensureSafeHubId(sessionId);
    return this.#withStore((store) => {
      const existingNotebook = store.getNotebook(safeSessionId);
      const existingMetadata = store.getHubSession(safeSessionId);
      const scenarioName = readOptionalString(body, "scenario_name") ?? existingNotebook?.scenario_name ?? "";
      if (!scenarioName) {
        throw new ResearchHubError("scenario_name is required when creating a hub session", 400);
      }

      store.upsertNotebook({
        sessionId: safeSessionId,
        scenarioName,
        currentObjective: readOptionalString(body, "current_objective") ?? existingNotebook?.current_objective,
        currentHypotheses: readOptionalStringList(body, "current_hypotheses") ?? existingNotebook?.current_hypotheses,
        bestRunId: readOptionalString(body, "best_run_id") ?? existingNotebook?.best_run_id,
        bestGeneration: readOptionalInteger(body, "best_generation") ?? existingNotebook?.best_generation,
        bestScore: readOptionalNumber(body, "best_score") ?? existingNotebook?.best_score,
        unresolvedQuestions: readOptionalStringList(body, "unresolved_questions") ?? existingNotebook?.unresolved_questions,
        operatorObservations:
          readOptionalStringList(body, "operator_observations") ?? existingNotebook?.operator_observations,
        followUps: readOptionalStringList(body, "follow_ups") ?? existingNotebook?.follow_ups,
      });
      store.upsertHubSession(safeSessionId, {
        owner: readOptionalString(body, "owner") ?? existingMetadata?.owner ?? "",
        status: readOptionalString(body, "status") ?? existingMetadata?.status ?? "active",
        leaseExpiresAt: readOptionalString(body, "lease_expires_at") ?? existingMetadata?.lease_expires_at ?? "",
        lastHeartbeatAt: existingMetadata?.last_heartbeat_at ?? nowIso(),
        shared: readOptionalBoolean(body, "shared") ?? existingMetadata?.shared ?? false,
        externalLink: readOptionalString(body, "external_link") ?? existingMetadata?.external_link ?? "",
        metadata: readOptionalRecord(body, "metadata") ?? existingMetadata?.metadata ?? {},
      });

      const notebook = store.getNotebook(safeSessionId);
      if (!notebook) {
        throw new ResearchHubError(`Failed to persist notebook for session ${safeSessionId}`, 500);
      }
      this.#artifacts.writeNotebook(safeSessionId, notebook as unknown as Record<string, unknown>);
      return {
        ...this.#composeSession(notebook, store.getHubSession(safeSessionId)),
        artifact_path: join(this.#runsRoot, "sessions", safeSessionId, "notebook.json"),
      };
    });
  }

  heartbeatSession(sessionId: string, body: Record<string, unknown>): Record<string, unknown> {
    const safeSessionId = ensureSafeHubId(sessionId);
    return this.#withStore((store) => {
      const notebook = store.getNotebook(safeSessionId);
      if (!notebook) {
        throw new ResearchHubError(`Notebook not found for session ${safeSessionId}`, 404);
      }
      const leaseSeconds = readOptionalInteger(body, "lease_seconds");
      const leaseExpiresAt = leaseSeconds !== undefined
        ? new Date(Date.now() + leaseSeconds * 1000).toISOString()
        : readOptionalString(body, "lease_expires_at");
      store.heartbeatHubSession(safeSessionId, {
        lastHeartbeatAt: nowIso(),
        leaseExpiresAt: leaseExpiresAt ?? null,
      });
      return this.#composeSession(notebook, store.getHubSession(safeSessionId));
    });
  }

  promotePackageFromRun(runId: string, body: Record<string, unknown>): Record<string, unknown> {
    return this.#withStore((store) => {
      const built = this.#buildPackageForRun(store, runId, body);
      this.#persistPackage(store, built.sharedPackage, built.strategyPackage);
      this.#persistPromotion(store, {
        event_id: `promo-${uid()}`,
        package_id: built.sharedPackage.package_id,
        source_run_id: runId,
        action: "promote",
        actor: readOptionalString(body, "actor") ?? "system",
        label: built.sharedPackage.promotion_level,
        created_at: nowIso(),
        metadata: { source_generation: built.sharedPackage.source_generation },
      });
      return built.sharedPackage;
    });
  }

  listPackages(): Record<string, unknown>[] {
    return this.#withStore((store) => store.listHubPackageRecords()
      .map((row) => this.#loadPackagePayload(row))
      .filter((packagePayload): packagePayload is Record<string, unknown> => packagePayload !== null));
  }

  getPackage(packageId: string): Record<string, unknown> {
    return this.#withStore((store) => {
      const row = store.getHubPackageRecord(ensureSafeHubId(packageId));
      const payload = row ? this.#loadPackagePayload(row) : null;
      if (!payload) {
        throw new ResearchHubError(`Hub package not found: ${packageId}`, 404);
      }
      return payload;
    });
  }

  adoptPackage(packageId: string, body: Record<string, unknown>): Record<string, unknown> {
    return this.#withStore((store) => {
      const row = store.getHubPackageRecord(ensureSafeHubId(packageId));
      if (!row || !row.strategy_package_path) {
        throw new ResearchHubError(`Strategy package payload not found for ${packageId}`, 404);
      }
      const rawPackage = readJsonRecord(join(this.#knowledgeRoot, row.strategy_package_path));
      if (!rawPackage) {
        throw new ResearchHubError(`Strategy package payload not found for ${packageId}`, 404);
      }
      const conflictPolicy = readConflictPolicy(body);
      const importResult = importStrategyPackage({
        rawPackage,
        artifacts: this.#artifacts,
        skillsRoot: this.#skillsRoot,
        conflictPolicy,
      });
      const event = {
        event_id: `promo-${uid()}`,
        package_id: packageId,
        source_run_id: row.source_run_id,
        action: "adopt",
        actor: readOptionalString(body, "actor") ?? "system",
        label: null,
        created_at: nowIso(),
        metadata: { conflict_policy: conflictPolicy },
      };
      this.#persistPromotion(store, event);
      return {
        import_result: importResult,
        promotion_event: event,
      };
    });
  }

  materializeResultFromRun(runId: string, body: Record<string, unknown>): Record<string, unknown> {
    return this.#withStore((store) => {
      const result = this.#buildResultForRun(store, runId, body);
      this.#persistResult(store, result);
      return result;
    });
  }

  listResults(): Record<string, unknown>[] {
    return this.#withStore((store) => store.listHubResultRecords()
      .map((row) => this.#loadResultPayload(row))
      .filter((result): result is Record<string, unknown> => result !== null));
  }

  getResult(resultId: string): Record<string, unknown> {
    return this.#withStore((store) => {
      const row = store.getHubResultRecord(ensureSafeHubId(resultId));
      const payload = row ? this.#loadResultPayload(row) : null;
      if (!payload) {
        throw new ResearchHubError(`Hub result not found: ${resultId}`, 404);
      }
      return payload;
    });
  }

  createPromotion(body: Record<string, unknown>): Record<string, unknown> {
    return this.#withStore((store) => {
      const event = {
        event_id: `promo-${uid()}`,
        package_id: readRequiredString(body, "package_id"),
        source_run_id: readRequiredString(body, "source_run_id"),
        action: readRequiredString(body, "action"),
        actor: readRequiredString(body, "actor"),
        label: readOptionalString(body, "label") ?? null,
        created_at: nowIso(),
        metadata: readOptionalRecord(body, "metadata") ?? {},
      };
      this.#persistPromotion(store, event);
      return event;
    });
  }

  feed(): Record<string, unknown> {
    return this.#withStore((store) => ({
      sessions: this.listSessions().slice(0, 5),
      packages: this.listPackages().slice(0, 5),
      results: this.listResults().slice(0, 5),
      promotions: store.listHubPromotionRecords().slice(0, 10).map((row) => formatPromotion(row)),
    }));
  }

  #buildPackageForRun(
    store: SQLiteStore,
    runId: string,
    body: Record<string, unknown>,
  ): { sharedPackage: Record<string, unknown>; strategyPackage: Record<string, unknown> } {
    const run = store.getRun(runId);
    if (!run) {
      throw new ResearchHubError(`Unknown run: ${runId}`, 404);
    }
    const best = bestGeneration(store.getGenerations(runId));
    if (!best) {
      throw new ResearchHubError(`No generation metrics found for run ${runId}`, 404);
    }
    const bestStrategy = parseStrategyOutput(
      store.getAgentOutputs(runId, best.generation_index)
        .filter((output) => output.role === "competitor")
        .at(-1)?.content ?? "",
    );
    const strategyPackage = exportStrategyPackage({
      scenarioName: run.scenario,
      artifacts: this.#artifacts,
      store,
    });
    const strategyMetadata = readRecordValue(strategyPackage.metadata);
    const sourceGeneration = best.generation_index;
    const normalizedStrategyPackage = {
      ...strategyPackage,
      best_strategy: bestStrategy,
      best_score: best.best_score,
      best_elo: best.elo,
      metadata: {
        ...strategyMetadata,
        source_run_id: runId,
        source_generation: sourceGeneration,
      },
    };
    const packageId = `pkg-${uid()}`;
    const family = scenarioFamily(run.scenario);
    const session = this.#sessionForPackage(store, readOptionalString(body, "session_id"), runId);
    const evidence = buildRunEvidence({
      store,
      knowledgeRoot: this.#knowledgeRoot,
      scenarioName: run.scenario,
      runId,
      generations: store.getGenerations(runId),
    });
    const compatibilityTags = readOptionalStringList(body, "compatibility_tags")
      ?? [run.scenario, family, run.agent_provider, run.executor_mode].filter(Boolean);
    return {
      strategyPackage: normalizedStrategyPackage,
      sharedPackage: {
        package_id: packageId,
        scenario_name: run.scenario,
        scenario_family: family,
        source_run_id: runId,
        source_generation: sourceGeneration,
        title: readOptionalString(body, "title") || `${humanize(run.scenario)} package from ${runId}`,
        description: readOptionalString(body, "description") || readStringValue(strategyPackage.description),
        strategy: bestStrategy,
        provider_summary: run.agent_provider,
        executor_summary: run.executor_mode,
        best_score: best.best_score,
        best_elo: best.elo,
        normalized_progress: evidence.normalizedProgress,
        weakness_summary: evidence.weaknessSummary,
        result_summary: `Best score ${best.best_score.toFixed(2)} on run ${runId}`,
        notebook_hypotheses: session?.current_hypotheses ?? [],
        linked_artifacts: evidence.linkedArtifacts,
        compatibility_tags: compatibilityTags,
        adoption_notes: readOptionalString(body, "adoption_notes") ?? "",
        promotion_level: readOptionalString(body, "promotion_level") ?? "experimental",
        created_at: nowIso(),
        metadata: {
          strategy_package_format_version: readNumberValue(strategyPackage.format_version, 1),
          source_session_id: session?.session_id ?? null,
        },
      },
    };
  }

  #buildResultForRun(
    store: SQLiteStore,
    runId: string,
    body: Record<string, unknown>,
  ): Record<string, unknown> {
    const run = store.getRun(runId);
    if (!run) {
      throw new ResearchHubError(`Unknown run: ${runId}`, 404);
    }
    const generations = store.getGenerations(runId);
    const best = bestGeneration(generations);
    if (!best) {
      throw new ResearchHubError(`No generation metrics found for run ${runId}`, 404);
    }
    const family = scenarioFamily(run.scenario);
    const evidence = buildRunEvidence({
      store,
      knowledgeRoot: this.#knowledgeRoot,
      scenarioName: run.scenario,
      runId,
      generations,
    });
    return {
      result_id: `res-${uid()}`,
      scenario_name: run.scenario,
      run_id: runId,
      package_id: readOptionalString(body, "package_id") ?? null,
      title: readOptionalString(body, "title") || `${humanize(run.scenario)} result for ${runId}`,
      summary: `Run ${runId} on ${run.scenario}: best score ${best.best_score.toFixed(2)}, `
        + `${generations.length} generation(s), ${evidence.normalizedProgress}.`,
      best_score: best.best_score,
      best_elo: best.elo,
      normalized_progress: evidence.normalizedProgress,
      cost_summary: evidence.costSummary,
      weakness_summary: evidence.weaknessSummary,
      consultation_summary: evidence.consultationSummary,
      friction_signals: evidence.frictionSignals,
      delight_signals: evidence.delightSignals,
      created_at: nowIso(),
      tags: [run.scenario, family, run.agent_provider].filter(Boolean),
      metadata: {
        scenario_family: family,
        agent_provider: run.agent_provider,
        executor_mode: run.executor_mode,
        linked_artifacts: evidence.linkedArtifacts,
      },
    };
  }

  #persistPackage(
    store: SQLiteStore,
    sharedPackage: Record<string, unknown>,
    strategyPackage: Record<string, unknown>,
  ): void {
    const packageId = ensureSafeHubId(readRequiredString(sharedPackage, "package_id"));
    const packageDir = join(this.#knowledgeRoot, "_hub", "packages", packageId);
    const payloadPath = join(packageDir, "shared_package.json");
    const strategyPath = join(packageDir, "strategy_package.json");
    writeJson(payloadPath, sharedPackage);
    writeJson(strategyPath, strategyPackage);
    store.saveHubPackageRecord({
      packageId,
      scenarioName: readRequiredString(sharedPackage, "scenario_name"),
      scenarioFamily: readOptionalString(sharedPackage, "scenario_family") ?? "",
      sourceRunId: readOptionalString(sharedPackage, "source_run_id") ?? "",
      sourceGeneration: readOptionalInteger(sharedPackage, "source_generation") ?? 0,
      title: readOptionalString(sharedPackage, "title") ?? "",
      description: readOptionalString(sharedPackage, "description") ?? "",
      promotionLevel: readOptionalString(sharedPackage, "promotion_level") ?? "experimental",
      bestScore: readOptionalNumber(sharedPackage, "best_score") ?? 0,
      bestElo: readOptionalNumber(sharedPackage, "best_elo") ?? 0,
      payloadPath: relative(this.#knowledgeRoot, payloadPath),
      strategyPackagePath: relative(this.#knowledgeRoot, strategyPath),
      tags: readOptionalStringList(sharedPackage, "compatibility_tags") ?? [],
      metadata: readOptionalRecord(sharedPackage, "metadata") ?? {},
      createdAt: readOptionalString(sharedPackage, "created_at") ?? nowIso(),
    });
  }

  #persistResult(store: SQLiteStore, result: Record<string, unknown>): void {
    const resultId = ensureSafeHubId(readRequiredString(result, "result_id"));
    const path = join(this.#knowledgeRoot, "_hub", "results", `${resultId}.json`);
    writeJson(path, result);
    store.saveHubResultRecord({
      resultId,
      scenarioName: readRequiredString(result, "scenario_name"),
      runId: readOptionalString(result, "run_id") ?? "",
      packageId: readOptionalString(result, "package_id") ?? null,
      title: readOptionalString(result, "title") ?? "",
      bestScore: readOptionalNumber(result, "best_score") ?? 0,
      bestElo: readOptionalNumber(result, "best_elo") ?? 0,
      payloadPath: relative(this.#knowledgeRoot, path),
      tags: readOptionalStringList(result, "tags") ?? [],
      metadata: readOptionalRecord(result, "metadata") ?? {},
      createdAt: readOptionalString(result, "created_at") ?? nowIso(),
    });
  }

  #persistPromotion(store: SQLiteStore, event: Record<string, unknown>): void {
    const eventId = ensureSafeHubId(readRequiredString(event, "event_id"));
    const path = join(this.#knowledgeRoot, "_hub", "promotions", `${eventId}.json`);
    writeJson(path, event);
    store.saveHubPromotionRecord({
      eventId,
      packageId: readOptionalString(event, "package_id") ?? "",
      sourceRunId: readOptionalString(event, "source_run_id") ?? "",
      action: readOptionalString(event, "action") ?? "",
      actor: readOptionalString(event, "actor") ?? "",
      label: readOptionalString(event, "label") ?? null,
      metadata: readOptionalRecord(event, "metadata") ?? {},
      createdAt: readOptionalString(event, "created_at") ?? nowIso(),
    });
  }

  #loadPackagePayload(row: HubPackageRecordRow): Record<string, unknown> | null {
    return readJsonRecord(join(this.#knowledgeRoot, row.payload_path));
  }

  #loadResultPayload(row: HubResultRecordRow): Record<string, unknown> | null {
    return readJsonRecord(join(this.#knowledgeRoot, row.payload_path));
  }

  #loadSessionFromStore(store: SQLiteStore, sessionId: string): Record<string, unknown> | null {
    const notebook = store.getNotebook(sessionId);
    if (!notebook) {
      return null;
    }
    return this.#composeSession(notebook, store.getHubSession(sessionId));
  }

  #sessionForPackage(
    store: SQLiteStore,
    sessionId: string | undefined,
    runId: string,
  ): NotebookRow | null {
    if (sessionId) {
      return store.getNotebook(ensureSafeHubId(sessionId));
    }
    return store.listNotebooks().find((notebook) => notebook.best_run_id === runId) ?? null;
  }

  #composeSession(notebook: NotebookRow, metadata: HubSessionRow | null): Record<string, unknown> {
    return {
      session_id: notebook.session_id,
      scenario_name: notebook.scenario_name,
      owner: metadata?.owner ?? "",
      status: metadata?.status ?? "active",
      lease_expires_at: metadata?.lease_expires_at ?? "",
      last_heartbeat_at: metadata?.last_heartbeat_at || notebook.updated_at || notebook.created_at,
      current_objective: notebook.current_objective,
      current_hypotheses: notebook.current_hypotheses,
      best_run_id: notebook.best_run_id,
      best_generation: notebook.best_generation,
      best_score: notebook.best_score,
      unresolved_questions: notebook.unresolved_questions,
      operator_observations: notebook.operator_observations,
      follow_ups: notebook.follow_ups,
      shared: metadata?.shared ?? false,
      external_link: metadata?.external_link ?? "",
      metadata: metadata?.metadata ?? {},
    };
  }

  #withStore<T>(fn: (store: SQLiteStore) => T): T {
    const store = this.#openStore();
    try {
      return fn(store);
    } finally {
      store.close();
    }
  }
}

function formatPromotion(row: HubPromotionRecordRow): Record<string, unknown> {
  return {
    event_id: row.event_id,
    package_id: row.package_id,
    source_run_id: row.source_run_id,
    action: row.action,
    actor: row.actor,
    label: row.label,
    created_at: row.created_at,
    metadata: row.metadata,
  };
}

function bestGeneration(generations: GenerationRow[]): GenerationRow | null {
  return generations.reduce<GenerationRow | null>((best, generation) => {
    if (!best) return generation;
    if (generation.best_score > best.best_score) return generation;
    if (
      generation.best_score === best.best_score
      && generation.generation_index > best.generation_index
    ) {
      return generation;
    }
    return best;
  }, null);
}

function progressSummary(generations: GenerationRow[]): string {
  const advances = generations.filter((generation) => generation.gate_decision === "advance").length;
  const retries = generations.filter((generation) => generation.gate_decision === "retry").length;
  const rollbacks = generations.filter((generation) => generation.gate_decision === "rollback").length;
  const parts = [
    advances ? `${advances} advance(s)` : "",
    retries ? `${retries} retry(ies)` : "",
    rollbacks ? `${rollbacks} rollback(s)` : "",
  ].filter(Boolean);
  return parts.join(", ") || "No generations";
}

function buildRunEvidence(opts: {
  store: SQLiteStore;
  knowledgeRoot: string;
  scenarioName: string;
  runId: string;
  generations: GenerationRow[];
}): HubRunEvidence {
  const progressReport = readJsonRecord(join(
    opts.knowledgeRoot,
    opts.scenarioName,
    "progress_reports",
    `${opts.runId}.json`,
  ));
  const weaknessReport = readJsonRecord(join(
    opts.knowledgeRoot,
    opts.scenarioName,
    "weakness_reports",
    `${opts.runId}.json`,
  ));
  const facet = readJsonRecord(join(opts.knowledgeRoot, "analytics", "facets", `${opts.runId}.json`));
  return {
    normalizedProgress: progressSummaryFromReport(progressReport, progressSummary(opts.generations)),
    costSummary: costSummaryFromFacet(facet) ?? costSummaryFromProgressReport(progressReport) ?? "$0.00 total, 0 tokens",
    weaknessSummary: weaknessSummaryFromReport(weaknessReport),
    consultationSummary: consultationSummary(opts.store, opts.runId),
    frictionSignals: signalDescriptions(facet, "friction_signals"),
    delightSignals: signalDescriptions(facet, "delight_signals"),
    linkedArtifacts: linkedArtifacts(opts.knowledgeRoot, opts.scenarioName, opts.runId),
  };
}

function progressSummaryFromReport(report: Record<string, unknown> | null, fallback: string): string {
  if (!report) {
    return fallback;
  }
  const progress = readRecordValue(report.progress);
  const pctOfCeiling = numberFrom(progress.pct_of_ceiling);
  if (pctOfCeiling === null) {
    return fallback;
  }
  const advances = integerFrom(report.advances) ?? 0;
  const retries = integerFrom(report.retries) ?? 0;
  const rollbacks = integerFrom(report.rollbacks) ?? 0;
  return `${pctOfCeiling.toFixed(2)}% of ceiling, `
    + `${advances} advance(s), ${retries} retry(ies), ${rollbacks} rollback(s)`;
}

function costSummaryFromFacet(facet: Record<string, unknown> | null): string | null {
  if (!facet) {
    return null;
  }
  const totalCost = numberFrom(facet.total_cost_usd);
  const totalTokens = integerFrom(facet.total_tokens);
  if (totalCost === null || totalTokens === null) {
    return null;
  }
  return `$${totalCost.toFixed(2)} total, ${totalTokens} tokens`;
}

function costSummaryFromProgressReport(report: Record<string, unknown> | null): string | null {
  if (!report) {
    return null;
  }
  const cost = readRecordValue(report.cost);
  const totalCost = numberFrom(cost.total_cost_usd);
  const totalTokens = integerFrom(cost.total_tokens);
  if (totalCost === null || totalTokens === null) {
    return null;
  }
  return `$${totalCost.toFixed(2)} total, ${totalTokens} tokens`;
}

function weaknessSummaryFromReport(report: Record<string, unknown> | null): string {
  const weaknesses = report?.weaknesses;
  if (!Array.isArray(weaknesses)) {
    return "";
  }
  return weaknesses
    .slice(0, 3)
    .map((weakness) => {
      const record = readRecordValue(weakness);
      return readStringValue(record.title) || readStringValue(record.description);
    })
    .filter(Boolean)
    .join("; ");
}

function consultationSummary(store: SQLiteStore, runId: string): string {
  const consultations = store.getConsultationsForRun(runId);
  if (consultations.length === 0) {
    return "";
  }
  const totalCost = store.getTotalConsultationCost(runId);
  const latest = consultations.at(-1);
  const trigger = latest?.trigger.trim() ?? "";
  return trigger
    ? `${consultations.length} consultation(s), $${totalCost.toFixed(2)} total, latest trigger: ${trigger}`
    : `${consultations.length} consultation(s), $${totalCost.toFixed(2)} total`;
}

function signalDescriptions(facet: Record<string, unknown> | null, key: string): string[] {
  const signals = facet?.[key];
  if (!Array.isArray(signals)) {
    return [];
  }
  return signals
    .map((signal) => readStringValue(readRecordValue(signal).description))
    .filter(Boolean);
}

function parseStrategyOutput(raw: string): Record<string, unknown> {
  if (!raw) {
    return {};
  }
  try {
    const parsed = JSON.parse(raw) as unknown;
    return parsed && typeof parsed === "object" && !Array.isArray(parsed)
      ? parsed as Record<string, unknown>
      : { raw_output: raw };
  } catch {
    return { raw_output: raw };
  }
}

function linkedArtifacts(knowledgeRoot: string, scenarioName: string, runId: string): string[] {
  const candidates = [
    join(knowledgeRoot, scenarioName, "playbook.md"),
    join(knowledgeRoot, scenarioName, "reports", `${runId}.md`),
    join(knowledgeRoot, scenarioName, "progress_reports", `${runId}.json`),
    join(knowledgeRoot, scenarioName, "weakness_reports", `${runId}.json`),
    join(knowledgeRoot, "analytics", "facets", `${runId}.json`),
  ];
  return candidates
    .filter((path) => existsSync(path))
    .map((path) => relative(knowledgeRoot, path));
}

function scenarioFamily(scenarioName: string): string {
  const ScenarioClass = SCENARIO_REGISTRY[scenarioName];
  if (!ScenarioClass) {
    return "";
  }
  try {
    return detectFamily(new ScenarioClass()) ?? "";
  } catch {
    return "";
  }
}

function readConflictPolicy(body: Record<string, unknown>): ConflictPolicy {
  const value = readOptionalString(body, "conflict_policy") ?? "merge";
  if (!CONFLICT_POLICIES.has(value as ConflictPolicy)) {
    throw new ResearchHubError("conflict_policy must be one of overwrite, merge, skip", 422);
  }
  return value as ConflictPolicy;
}

function readRequiredString(body: Record<string, unknown>, key: string): string {
  const value = readOptionalString(body, key);
  if (!value) {
    throw new ResearchHubError(`${key} is required`, 422);
  }
  return value;
}

function readOptionalString(body: Record<string, unknown>, key: string): string | undefined {
  const value = body[key];
  return typeof value === "string" ? value : undefined;
}

function readOptionalInteger(body: Record<string, unknown>, key: string): number | undefined {
  const value = body[key];
  return typeof value === "number" && Number.isInteger(value) ? value : undefined;
}

function readOptionalNumber(body: Record<string, unknown>, key: string): number | undefined {
  const value = body[key];
  return typeof value === "number" ? value : undefined;
}

function readOptionalBoolean(body: Record<string, unknown>, key: string): boolean | undefined {
  const value = body[key];
  return typeof value === "boolean" ? value : undefined;
}

function readOptionalStringList(body: Record<string, unknown>, key: string): string[] | undefined {
  const value = body[key];
  if (value === undefined) {
    return undefined;
  }
  if (!Array.isArray(value) || !value.every((entry) => typeof entry === "string")) {
    throw new ResearchHubError(`${key} must be a list of strings`, 422);
  }
  return value;
}

function readOptionalRecord(body: Record<string, unknown>, key: string): Record<string, unknown> | undefined {
  const value = body[key];
  if (value === undefined) {
    return undefined;
  }
  if (!value || typeof value !== "object" || Array.isArray(value)) {
    throw new ResearchHubError(`${key} must be an object`, 422);
  }
  return value as Record<string, unknown>;
}

function readRecordValue(value: unknown): Record<string, unknown> {
  return value && typeof value === "object" && !Array.isArray(value)
    ? value as Record<string, unknown>
    : {};
}

function readStringValue(value: unknown): string {
  return typeof value === "string" ? value : "";
}

function readNumberValue(value: unknown, fallback: number): number {
  return typeof value === "number" ? value : fallback;
}

function numberFrom(value: unknown): number | null {
  if (typeof value === "number" && Number.isFinite(value)) {
    return value;
  }
  if (typeof value === "string" && value.trim()) {
    const parsed = Number(value);
    return Number.isFinite(parsed) ? parsed : null;
  }
  return null;
}

function integerFrom(value: unknown): number | null {
  const parsed = numberFrom(value);
  return parsed === null ? null : Math.trunc(parsed);
}

function readJsonRecord(path: string): Record<string, unknown> | null {
  try {
    const parsed = JSON.parse(readFileSync(path, "utf-8")) as unknown;
    return parsed && typeof parsed === "object" && !Array.isArray(parsed)
      ? parsed as Record<string, unknown>
      : null;
  } catch {
    return null;
  }
}

function writeJson(path: string, payload: Record<string, unknown>): void {
  mkdirSync(dirname(path), { recursive: true });
  writeFileSync(path, JSON.stringify(payload, null, 2) + "\n", "utf-8");
}

function ensureSafeHubId(id: string): string {
  if (!SAFE_HUB_ID.test(id)) {
    throw new ResearchHubError(`invalid hub id: ${id}`, 422);
  }
  return id;
}

function uid(): string {
  return randomUUID().replace(/-/g, "").slice(0, 8);
}

function nowIso(): string {
  return new Date().toISOString();
}

function humanize(name: string): string {
  return name
    .split(/[_-]+/)
    .filter(Boolean)
    .map((part) => part.charAt(0).toUpperCase() + part.slice(1))
    .join(" ");
}
