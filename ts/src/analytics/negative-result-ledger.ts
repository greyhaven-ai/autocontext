import { stableDigest } from "../context-bundles/index.js";

export const FAILURE_KINDS = [
  "verification_failed",
  "score_regression",
  "pruned",
  "refused",
  "dead_end",
  "timeout",
  "harness_error",
  "unsafe_action",
] as const;

export const NEGATIVE_RESULT_DISPOSITIONS = ["caution", "hard_ban", "noise"] as const;
export const NEGATIVE_RESULT_APPLICABILITY_SCOPES = [
  "exact_bundle",
  "bundle_family",
  "scenario_local",
  "cross_scenario",
  "context_unknown",
] as const;
export const NEGATIVE_RESULT_RETEST_OUTCOMES = [
  "confirmed",
  "not_reproduced",
  "inconclusive",
] as const;

export type FailureKind = (typeof FAILURE_KINDS)[number];
export type NegativeResultDisposition = (typeof NEGATIVE_RESULT_DISPOSITIONS)[number];
export type NegativeResultApplicabilityScope = (typeof NEGATIVE_RESULT_APPLICABILITY_SCOPES)[number];
export type NegativeResultRetestOutcome = (typeof NEGATIVE_RESULT_RETEST_OUTCOMES)[number];
export type NegativeResultApplicabilityState =
  | "applicable"
  | "qualified"
  | "retest_due"
  | "superseded"
  | "excluded";

export interface NegativeResultEventInput {
  event_id?: string;
  event_type?: string;
  event?: string;
  timestamp?: string;
  ts?: string;
  seq?: number;
  branch_id?: string;
  parent_branch_id?: string;
  hypothesis_node_id?: string;
  generation_index?: number;
  reason?: string;
  payload?: Record<string, unknown>;
}

export interface NegativeEvidenceReference {
  uri: string;
  summary: string;
}

export interface NegativeBranchLineageEdge {
  parent_branch_id: string;
  child_branch_id: string;
  event_id: string | null;
}

export interface NegativeComponentDependency {
  component_kind: string;
  key: string;
  digest: string;
}

export interface NegativeResultContext {
  scenario_name: string | null;
  context_bundle_digest: string | null;
  context_bundle_family: string | null;
  evaluator_epoch: string | null;
  verifier_digest: string | null;
  trial_cohort: string | null;
  component_dependencies: NegativeComponentDependency[];
  environment_fingerprint: string | null;
}

export interface NegativeResultApplicabilityContext {
  scenario_name: string;
  context_bundle_digest: string;
  context_bundle_family?: string | null;
  evaluator_epoch: string;
  verifier_digest?: string | null;
  trial_cohort?: string | null;
  component_digests?: Record<string, string>;
  environment_fingerprint?: string | null;
  observed_at?: string | null;
  stronger_evidence_available?: boolean;
}

export interface NegativeResultApplicability {
  state: NegativeResultApplicabilityState;
  effective_disposition: NegativeResultDisposition | null;
  reason: string;
  retest_eligible: boolean;
}

export interface NegativeResultEntry {
  result_id: string;
  branch_id: string;
  hypothesis_node_id: string | null;
  generation_index: number | null;
  occurred_at: string;
  failure_kind: FailureKind;
  disposition: NegativeResultDisposition;
  reason: string;
  score_delta: number | null;
  evaluated_seeds: string[];
  evaluated_probes: string[];
  branch_lineage: NegativeBranchLineageEdge[];
  evidence_refs: NegativeEvidenceReference[];
  applicability_scope: NegativeResultApplicabilityScope;
  context: NegativeResultContext;
  evidence_expires_at: string | null;
  safety_policy_authority: string | null;
  retest_of_result_id: string | null;
  retest_outcome: NegativeResultRetestOutcome | null;
  superseded_by_result_id: string | null;
}

export interface FailureModeSummary {
  failure_kind: FailureKind;
  disposition: NegativeResultDisposition;
  count: number;
  result_ids: string[];
}

export interface NegativeResultLedger {
  schema_version: 2;
  run_id: string;
  generated_at: string;
  entries: NegativeResultEntry[];
  failure_mode_summary: FailureModeSummary[];
}

export interface BuildNegativeResultLedgerInput {
  runId: string;
  events: NegativeResultEventInput[];
  scenarioName: string;
  contextBundleDigest: string;
  evaluatorEpoch: string;
  generatedAt?: string;
  contextBundleFamily?: string;
  verifierDigest?: string;
  trialCohort?: string;
  componentDependencies?: NegativeComponentDependency[];
  environmentFingerprint?: string;
}

const NEGATIVE_EVENTS = new Set([
  "branch_failed",
  "branch_pruned",
  "branch_rejected",
  "candidate_rejected",
  "evaluation_failed",
  "gate_rollback",
  "harness_refused",
]);
const EVENT_FAILURE_KIND: Record<string, FailureKind> = {
  branch_pruned: "pruned",
  branch_rejected: "dead_end",
  candidate_rejected: "verification_failed",
  evaluation_failed: "verification_failed",
  gate_rollback: "score_regression",
  harness_refused: "refused",
};
const FAILURE_KIND_SET = new Set<string>(FAILURE_KINDS);
const DISPOSITION_SET = new Set<string>(NEGATIVE_RESULT_DISPOSITIONS);
const APPLICABILITY_SCOPE_SET = new Set<string>(NEGATIVE_RESULT_APPLICABILITY_SCOPES);
const RETEST_OUTCOME_SET = new Set<string>(NEGATIVE_RESULT_RETEST_OUTCOMES);

export function buildNegativeResultLedger(
  input: BuildNegativeResultLedgerInput,
): NegativeResultLedger {
  const defaults: NegativeResultContext = {
    scenario_name: input.scenarioName ?? null,
    context_bundle_digest: input.contextBundleDigest ?? null,
    context_bundle_family: input.contextBundleFamily ?? null,
    evaluator_epoch: input.evaluatorEpoch ?? null,
    verifier_digest: input.verifierDigest ?? null,
    trial_cohort: input.trialCohort ?? null,
    component_dependencies: input.componentDependencies ?? [],
    environment_fingerprint: input.environmentFingerprint ?? null,
  };
  const entries = input.events.flatMap((event, eventIndex) => {
    const entry = entryFromEvent(event, defaults, eventIndex);
    return entry ? [entry] : [];
  });
  return parseNegativeResultLedger({
    schema_version: 2,
    run_id: input.runId,
    generated_at: input.generatedAt ?? new Date().toISOString(),
    entries,
    failure_mode_summary: failureModeSummary(entries),
  });
}

export function parseNegativeResultLedger(value: unknown): NegativeResultLedger {
  const original = record(value, "negative result ledger");
  const ledger = original.schema_version === 1 ? migrateV1Ledger(original) : original;
  exact(ledger, ["schema_version", "run_id", "generated_at", "entries", "failure_mode_summary"]);
  if (ledger.schema_version !== 2) throw new Error("schema_version must be 2");
  const entries = array(ledger.entries, "entries").map(parseEntry);
  ensureUniqueResultIds(entries);
  return {
    schema_version: 2,
    run_id: string(ledger.run_id, "run_id"),
    generated_at: string(ledger.generated_at, "generated_at"),
    entries,
    failure_mode_summary: array(ledger.failure_mode_summary, "failure_mode_summary").map(
      parseFailureModeSummary,
    ),
  };
}

export function renderNegativeResultLessons(
  ledger: NegativeResultLedger,
  opts: { maxEntries?: number; applicabilityContext?: NegativeResultApplicabilityContext } = {},
): string {
  const rank: Record<NegativeResultDisposition, number> = { hard_ban: 0, caution: 1, noise: 2 };
  return ledger.entries
    .filter((entry) => entry.disposition !== "noise" && entry.evidence_refs.length > 0)
    .map((entry) => ({
      entry,
      applicability: evaluateNegativeResultApplicability(entry, opts.applicabilityContext),
    }))
    .filter(({ applicability }) => applicability.state !== "excluded" && applicability.state !== "superseded")
    .sort((left, right) =>
      rank[left.entry.disposition] - rank[right.entry.disposition]
      || left.entry.result_id.localeCompare(right.entry.result_id)
    )
    .slice(0, opts.maxEntries ?? 4)
    .map(({ entry, applicability }) => {
      const evidence = entry.evidence_refs.slice(0, 2).map((ref) => ref.summary).join("; ");
      const delta = entry.score_delta === null ? "" : `, delta=${entry.score_delta}`;
      const prefix = applicability.effective_disposition === "hard_ban" ? "Hard ban" : "Caution";
      const suffix = applicability.effective_disposition === "hard_ban"
        ? "do not repeat without new evidence"
        : "not a ban; explore only with differentiating evidence";
      return `- ${prefix}: ${entry.failure_kind} on ${entry.branch_id} (${entry.result_id}${delta}) — ${entry.reason}; applicability: ${applicability.reason}; evidence: ${evidence}; ${suffix}.`;
    })
    .join("\n");
}

export function negativeResultLedgerToMarkdown(
  ledger: NegativeResultLedger,
  applicabilityContext?: NegativeResultApplicabilityContext,
): string {
  const summary = ledger.failure_mode_summary.map(
    (item) => `- ${item.failure_kind}/${item.disposition}: ${item.count} (${item.result_ids.join(", ")})`,
  );
  return [
    `# Negative Result Ledger: ${ledger.run_id}`,
    "",
    "## Failure Modes",
    ...(summary.length ? summary : ["- None"]),
    "",
    "## Prompt Lessons",
    renderNegativeResultLessons(ledger, { applicabilityContext }) || "- None",
    "",
  ].join("\n");
}

export function evaluateNegativeResultApplicability(
  entry: NegativeResultEntry,
  current?: NegativeResultApplicabilityContext,
): NegativeResultApplicability {
  if (entry.disposition === "noise") {
    return applicability("excluded", null, "noise is retained for inspection but not injected", false);
  }
  if (entry.superseded_by_result_id) {
    return applicability("superseded", null, `superseded by retest ${entry.superseded_by_result_id}`, false);
  }
  if (entry.applicability_scope === "context_unknown") {
    return applicability(
      "qualified",
      "caution",
      "legacy evidence has unknown context and cannot impose a hard ban",
      true,
    );
  }
  if (!current) {
    return applicability(
      "qualified",
      "caution",
      "current context was not supplied; applicability is unverified",
      true,
    );
  }
  const mismatch = contextMismatch(entry.applicability_scope, entry.context, current);
  if (mismatch) return applicability("retest_due", "caution", mismatch, true);
  if (entry.evidence_expires_at && isAtOrAfter(current.observed_at, entry.evidence_expires_at)) {
    return applicability("retest_due", "caution", "evidence age limit was reached", true);
  }
  if (current.stronger_evidence_available) {
    return applicability("retest_due", "caution", "stronger evidence is available", true);
  }
  if (
    entry.disposition === "hard_ban"
    && entry.applicability_scope === "cross_scenario"
    && !entry.safety_policy_authority
  ) {
    return applicability(
      "qualified",
      "caution",
      "cross-scenario hard ban lacks safety-policy authority",
      true,
    );
  }
  return applicability(
    "applicable",
    entry.disposition,
    `matched ${entry.applicability_scope} context`,
    true,
  );
}

export function linkNegativeResultRetest(
  ledger: NegativeResultLedger,
  originalResultId: string,
  retestEntry: NegativeResultEntry,
): NegativeResultLedger {
  ensureUniqueResultIds(ledger.entries);
  if (retestEntry.retest_of_result_id !== originalResultId || retestEntry.retest_outcome === null) {
    throw new Error("retest entry must link to the original and declare an outcome");
  }
  if (ledger.entries.some((entry) => entry.result_id === retestEntry.result_id)) {
    throw new Error(`retest result already exists: ${retestEntry.result_id}`);
  }
  let found = false;
  const entries = ledger.entries.map((entry) => {
    if (entry.result_id !== originalResultId) return entry;
    found = true;
    return {
      ...entry,
      superseded_by_result_id: retestCanSupersede(entry, retestEntry)
        ? retestEntry.result_id
        : entry.superseded_by_result_id,
    };
  });
  if (!found) throw new Error(`unknown original result: ${originalResultId}`);
  entries.push(retestEntry);
  return { ...ledger, entries, failure_mode_summary: failureModeSummary(entries) };
}

function parseEntry(value: unknown): NegativeResultEntry {
  const item = record(value, "negative result entry");
  exact(item, [
    "result_id",
    "branch_id",
    "hypothesis_node_id",
    "generation_index",
    "occurred_at",
    "failure_kind",
    "disposition",
    "reason",
    "score_delta",
    "evaluated_seeds",
    "evaluated_probes",
    "branch_lineage",
    "evidence_refs",
    "applicability_scope",
    "context",
    "evidence_expires_at",
    "safety_policy_authority",
    "retest_of_result_id",
    "retest_outcome",
    "superseded_by_result_id",
  ]);
  return {
    result_id: string(item.result_id, "result_id"),
    branch_id: string(item.branch_id, "branch_id"),
    hypothesis_node_id: nullableString(item.hypothesis_node_id, "hypothesis_node_id"),
    generation_index: nullableNonNegativeInteger(item.generation_index, "generation_index"),
    occurred_at: string(item.occurred_at, "occurred_at"),
    failure_kind: failureKind(item.failure_kind),
    disposition: disposition(item.disposition),
    reason: string(item.reason, "reason"),
    score_delta: nullableNumber(item.score_delta, "score_delta"),
    evaluated_seeds: array(item.evaluated_seeds, "evaluated_seeds").map((seed) => string(seed, "seed")),
    evaluated_probes: array(item.evaluated_probes, "evaluated_probes").map((probe) => string(probe, "probe")),
    branch_lineage: array(item.branch_lineage, "branch_lineage").map(parseLineageEdge),
    evidence_refs: array(item.evidence_refs, "evidence_refs").map(parseEvidenceReference),
    applicability_scope: applicabilityScope(item.applicability_scope),
    context: parseNegativeResultContext(item.context),
    evidence_expires_at: nullableString(item.evidence_expires_at, "evidence_expires_at"),
    safety_policy_authority: nullableString(item.safety_policy_authority, "safety_policy_authority"),
    retest_of_result_id: nullableString(item.retest_of_result_id, "retest_of_result_id"),
    retest_outcome: nullableRetestOutcome(item.retest_outcome),
    superseded_by_result_id: nullableString(item.superseded_by_result_id, "superseded_by_result_id"),
  };
}

function parseNegativeResultContext(value: unknown): NegativeResultContext {
  const item = record(value, "negative result context");
  exact(item, [
    "scenario_name",
    "context_bundle_digest",
    "context_bundle_family",
    "evaluator_epoch",
    "verifier_digest",
    "trial_cohort",
    "component_dependencies",
    "environment_fingerprint",
  ]);
  return {
    scenario_name: nullableString(item.scenario_name, "scenario_name"),
    context_bundle_digest: nullableString(item.context_bundle_digest, "context_bundle_digest"),
    context_bundle_family: nullableString(item.context_bundle_family, "context_bundle_family"),
    evaluator_epoch: nullableString(item.evaluator_epoch, "evaluator_epoch"),
    verifier_digest: nullableString(item.verifier_digest, "verifier_digest"),
    trial_cohort: nullableString(item.trial_cohort, "trial_cohort"),
    component_dependencies: array(item.component_dependencies, "component_dependencies").map(
      parseComponentDependency,
    ),
    environment_fingerprint: nullableString(item.environment_fingerprint, "environment_fingerprint"),
  };
}

function parseComponentDependency(value: unknown): NegativeComponentDependency {
  const item = record(value, "component dependency");
  exact(item, ["component_kind", "key", "digest"]);
  return {
    component_kind: string(item.component_kind, "component_kind"),
    key: string(item.key, "key"),
    digest: string(item.digest, "digest"),
  };
}

function parseLineageEdge(value: unknown): NegativeBranchLineageEdge {
  const item = record(value, "branch lineage edge");
  exact(item, ["parent_branch_id", "child_branch_id", "event_id"]);
  return {
    parent_branch_id: string(item.parent_branch_id, "parent_branch_id"),
    child_branch_id: string(item.child_branch_id, "child_branch_id"),
    event_id: nullableString(item.event_id, "event_id"),
  };
}

function parseEvidenceReference(value: unknown): NegativeEvidenceReference {
  const item = record(value, "evidence reference");
  exact(item, ["uri", "summary"]);
  return {
    uri: string(item.uri, "uri"),
    summary: string(item.summary, "summary"),
  };
}

function parseFailureModeSummary(value: unknown): FailureModeSummary {
  const item = record(value, "failure mode summary");
  exact(item, ["failure_kind", "disposition", "count", "result_ids"]);
  return {
    failure_kind: failureKind(item.failure_kind),
    disposition: disposition(item.disposition),
    count: nonNegativeInteger(item.count, "count"),
    result_ids: array(item.result_ids, "result_ids").map((id) => string(id, "result_id")),
  };
}

function entryFromEvent(
  event: NegativeResultEventInput,
  defaults: NegativeResultContext,
  eventIndex: number,
): NegativeResultEntry | null {
  const payload = event.payload ?? {};
  const eventType = event.event_type ?? event.event ?? "";
  const kind = eventFailureKind(payload, eventType);
  const isNegativeEvent = kind !== null || NEGATIVE_EVENTS.has(eventType);
  if (isNegativeEvent === false) return null;
  const branchId = event.branch_id ?? maybeString(payload.branch_id) ?? "";
  if (branchId.length === 0) return null;
  const eventId = event.event_id ?? (event.seq !== undefined ? `seq-${event.seq}` : "");
  const fallbackResultId = eventId || fallbackResultIdForEvent(event, eventIndex);
  const resultId = maybeString(payload.result_id) ?? fallbackResultId;
  const context = contextFromPayload(payload, defaults);
  const explicitScope = maybeApplicabilityScope(payload.applicability_scope);
  const inferredScope: NegativeResultApplicabilityScope = context.scenario_name
    && context.context_bundle_digest
    && context.evaluator_epoch
    ? "exact_bundle"
    : "context_unknown";
  return {
    result_id: resultId,
    branch_id: branchId,
    hypothesis_node_id: maybeString(payload.hypothesis_node_id) ?? event.hypothesis_node_id ?? null,
    generation_index: nonNegativeIntegerOrNull(payload.generation_index ?? event.generation_index),
    occurred_at: event.timestamp ?? event.ts ?? new Date().toISOString(),
    failure_kind: kind ?? EVENT_FAILURE_KIND[eventType] ?? "dead_end",
    disposition: eventDisposition(payload.disposition),
    reason: maybeString(payload.reason) ?? event.reason ?? "Negative branch result recorded.",
    score_delta: scoreDelta(payload),
    evaluated_seeds: stringArray(payload.evaluated_seeds ?? payload.seeds),
    evaluated_probes: stringArray(payload.evaluated_probes ?? payload.probes),
    branch_lineage: branchLineage(event, payload, eventId, branchId),
    evidence_refs: evidenceRefs(payload),
    applicability_scope: explicitScope ?? inferredScope,
    context,
    evidence_expires_at: maybeString(payload.evidence_expires_at) ?? null,
    safety_policy_authority: maybeString(payload.safety_policy_authority) ?? null,
    retest_of_result_id: maybeString(payload.retest_of_result_id) ?? null,
    retest_outcome: maybeRetestOutcome(payload.retest_outcome),
    superseded_by_result_id: maybeString(payload.superseded_by_result_id) ?? null,
  };
}

function fallbackResultIdForEvent(event: NegativeResultEventInput, eventIndex: number): string {
  return `negative-${stableDigest({ event, event_index: eventIndex })}`;
}

function ensureUniqueResultIds(entries: readonly NegativeResultEntry[]): void {
  const seen = new Set<string>();
  for (const entry of entries) {
    if (seen.has(entry.result_id)) throw new Error(`duplicate negative result ID: ${entry.result_id}`);
    seen.add(entry.result_id);
  }
}

function failureModeSummary(entries: NegativeResultEntry[]): FailureModeSummary[] {
  const groups = new Map<string, FailureModeSummary>();
  for (const entry of entries) {
    const key = `${entry.failure_kind}:${entry.disposition}`;
    const existing = groups.get(key) ?? {
      failure_kind: entry.failure_kind,
      disposition: entry.disposition,
      count: 0,
      result_ids: [],
    };
    existing.count += 1;
    existing.result_ids.push(entry.result_id);
    groups.set(key, existing);
  }
  return [...groups.values()].sort((left, right) =>
    `${left.failure_kind}:${left.disposition}`.localeCompare(`${right.failure_kind}:${right.disposition}`),
  );
}

function eventFailureKind(payload: Record<string, unknown>, eventType: string): FailureKind | null {
  const value = maybeString(payload.failure_kind);
  if (value && FAILURE_KIND_SET.has(value)) return value as FailureKind;
  return EVENT_FAILURE_KIND[eventType] ?? null;
}

function eventDisposition(value: unknown): NegativeResultDisposition {
  const result = maybeString(value);
  return result && DISPOSITION_SET.has(result) ? result as NegativeResultDisposition : "caution";
}

function scoreDelta(payload: Record<string, unknown>): number | null {
  const explicit = maybeNumber(payload.score_delta);
  if (explicit !== undefined) return roundSix(explicit);
  const score = maybeNumber(payload.score);
  const baseline = maybeNumber(payload.baseline_score);
  return score !== undefined && baseline !== undefined ? roundSix(score - baseline) : null;
}

function branchLineage(
  event: NegativeResultEventInput,
  payload: Record<string, unknown>,
  eventId: string,
  branchId: string,
): NegativeBranchLineageEdge[] {
  if (Array.isArray(payload.branch_lineage)) {
    return payload.branch_lineage.flatMap((edge) => {
      const parsed = lineageEdgeFromRecord(edge);
      return parsed ? [parsed] : [];
    });
  }
  const parent = event.parent_branch_id ?? maybeString(payload.parent_branch_id);
  return parent ? [{ parent_branch_id: parent, child_branch_id: branchId, event_id: eventId || null }] : [];
}

function lineageEdgeFromRecord(value: unknown): NegativeBranchLineageEdge | null {
  if (!value || typeof value !== "object" || Array.isArray(value)) return null;
  const item = value as Record<string, unknown>;
  const parent = maybeString(item.parent_branch_id);
  const child = maybeString(item.child_branch_id);
  if (!parent || !child) return null;
  return { parent_branch_id: parent, child_branch_id: child, event_id: maybeString(item.event_id) ?? null };
}

function evidenceRefs(payload: Record<string, unknown>): NegativeEvidenceReference[] {
  if (Array.isArray(payload.evidence_refs)) {
    return payload.evidence_refs.flatMap((item) => {
      const parsed = evidenceRefFromRecord(item);
      return parsed ? [parsed] : [];
    });
  }
  const uri = maybeString(payload.evidence_uri);
  const summary = maybeString(payload.evidence_summary);
  return uri && summary ? [{ uri, summary }] : [];
}

function evidenceRefFromRecord(value: unknown): NegativeEvidenceReference | null {
  if (!value || typeof value !== "object" || Array.isArray(value)) return null;
  const item = value as Record<string, unknown>;
  const uri = maybeString(item.uri);
  const summary = maybeString(item.summary);
  return uri && summary ? { uri, summary } : null;
}

function contextFromPayload(
  payload: Record<string, unknown>,
  defaults: NegativeResultContext,
): NegativeResultContext {
  const context = payload.context === undefined ? payload : record(payload.context, "negative result context");
  const dependencies = Array.isArray(context.component_dependencies)
    ? context.component_dependencies.flatMap((value) => {
      try {
        return [parseComponentDependency(value)];
      } catch {
        return [];
      }
    })
    : defaults.component_dependencies;
  return {
    scenario_name: maybeString(context.scenario_name) ?? defaults.scenario_name,
    context_bundle_digest: maybeString(context.context_bundle_digest) ?? defaults.context_bundle_digest,
    context_bundle_family: maybeString(context.context_bundle_family) ?? defaults.context_bundle_family,
    evaluator_epoch: maybeString(context.evaluator_epoch) ?? defaults.evaluator_epoch,
    verifier_digest: maybeString(context.verifier_digest) ?? defaults.verifier_digest,
    trial_cohort: maybeString(context.trial_cohort) ?? defaults.trial_cohort,
    component_dependencies: dependencies,
    environment_fingerprint: maybeString(context.environment_fingerprint) ?? defaults.environment_fingerprint,
  };
}

function applicability(
  state: NegativeResultApplicabilityState,
  effectiveDisposition: NegativeResultDisposition | null,
  reason: string,
  retestEligible: boolean,
): NegativeResultApplicability {
  return {
    state,
    effective_disposition: effectiveDisposition,
    reason,
    retest_eligible: retestEligible,
  };
}

function scopeMismatch(
  scope: NegativeResultApplicabilityScope,
  recorded: NegativeResultContext,
  current: NegativeResultApplicabilityContext,
): string | null {
  if (!recorded.scenario_name || !recorded.evaluator_epoch) return "recorded context is incomplete";
  if (scope === "exact_bundle" && recorded.context_bundle_digest !== current.context_bundle_digest) {
    return "context bundle changed";
  }
  if (
    scope === "bundle_family"
    && (!recorded.context_bundle_family || recorded.context_bundle_family !== current.context_bundle_family)
  ) {
    return "context bundle family changed";
  }
  if (
    ["exact_bundle", "bundle_family", "scenario_local"].includes(scope)
    && recorded.scenario_name !== current.scenario_name
  ) {
    return "scenario changed";
  }
  return null;
}

function contextMismatch(
  scope: NegativeResultApplicabilityScope,
  recorded: NegativeResultContext,
  current: NegativeResultApplicabilityContext,
): string | null {
  const mismatch = scopeMismatch(scope, recorded, current);
  if (mismatch) return mismatch;
  if (recorded.evaluator_epoch !== current.evaluator_epoch) return "evaluator epoch changed";
  if (recorded.verifier_digest && recorded.verifier_digest !== current.verifier_digest) {
    return "verifier changed";
  }
  if (recorded.trial_cohort && recorded.trial_cohort !== current.trial_cohort) {
    return "trial cohort changed";
  }
  if (recorded.environment_fingerprint && recorded.environment_fingerprint !== current.environment_fingerprint) {
    return "environment fingerprint changed";
  }
  for (const dependency of recorded.component_dependencies) {
    const key = `${dependency.component_kind}:${dependency.key}`;
    if (current.component_digests?.[key] !== dependency.digest) return `dependency ${key} changed`;
  }
  return null;
}

function retestCanSupersede(original: NegativeResultEntry, retest: NegativeResultEntry): boolean {
  if (retest.retest_outcome !== "not_reproduced") return false;
  if (retest.disposition === "noise") return false;
  if (retest.evidence_refs.length === 0
    || (retest.evaluated_seeds.length === 0 && retest.evaluated_probes.length === 0)) return false;
  const originalTime = parseIsoTimestamp(original.occurred_at);
  const retestTime = parseIsoTimestamp(retest.occurred_at);
  if (originalTime === null || retestTime === null || retestTime < originalTime) return false;
  if (original.safety_policy_authority !== retest.safety_policy_authority) return false;
  if (original.applicability_scope !== retest.applicability_scope) return false;

  const recorded = original.context;
  const candidate = retest.context;
  if (
    recorded.scenario_name !== candidate.scenario_name
    || recorded.context_bundle_digest !== candidate.context_bundle_digest
    || recorded.context_bundle_family !== candidate.context_bundle_family
    || recorded.evaluator_epoch !== candidate.evaluator_epoch
    || recorded.verifier_digest !== candidate.verifier_digest
    || recorded.trial_cohort !== candidate.trial_cohort
    || recorded.environment_fingerprint !== candidate.environment_fingerprint
  ) return false;

  const dependencyIdentity = (dependency: NegativeComponentDependency): string =>
    JSON.stringify([dependency.component_kind, dependency.key, dependency.digest]);
  const recordedDependencies = recorded.component_dependencies.map(dependencyIdentity).sort();
  const candidateDependencies = candidate.component_dependencies.map(dependencyIdentity).sort();
  return recordedDependencies.length === candidateDependencies.length
    && recordedDependencies.every((dependency, index) => dependency === candidateDependencies[index]);
}

function isAtOrAfter(observedAt: string | null | undefined, expiresAt: string): boolean {
  const observed = observedAt ? parseIsoTimestamp(observedAt) : Date.now();
  const expires = parseIsoTimestamp(expiresAt);
  return observed === null || expires === null || observed >= expires;
}

function parseIsoTimestamp(value: string): number | null {
  const hasTimeZone = /(?:[zZ]|[+-]\d{2}:?\d{2})$/.test(value);
  const normalized = hasTimeZone
    ? value
    : /^\d{4}-\d{2}-\d{2}$/.test(value)
      ? `${value}T00:00:00Z`
      : `${value}Z`;
  const parsed = Date.parse(normalized);
  return Number.isNaN(parsed) ? null : parsed;
}

function migrateV1Ledger(ledger: Record<string, unknown>): Record<string, unknown> {
  const entries = array(ledger.entries, "entries").map((value) => ({
    ...record(value, "negative result entry"),
    applicability_scope: "context_unknown",
    context: {
      scenario_name: null,
      context_bundle_digest: null,
      context_bundle_family: null,
      evaluator_epoch: null,
      verifier_digest: null,
      trial_cohort: null,
      component_dependencies: [],
      environment_fingerprint: null,
    },
    evidence_expires_at: null,
    safety_policy_authority: null,
    retest_of_result_id: null,
    retest_outcome: null,
    superseded_by_result_id: null,
  }));
  return { ...ledger, schema_version: 2, entries };
}

function record(value: unknown, label: string): Record<string, unknown> {
  if (!value || typeof value !== "object" || Array.isArray(value)) throw new Error(`${label} must be an object`);
  return value as Record<string, unknown>;
}

function exact(item: Record<string, unknown>, allowed: string[]): void {
  const allowedSet = new Set(allowed);
  const keys = Object.keys(item);
  const missing = allowed.filter((key) => !keys.includes(key));
  if (missing.length) throw new Error(`missing field(s): ${missing.sort().join(", ")}`);
  const extra = keys.filter((key) => !allowedSet.has(key));
  if (extra.length) throw new Error(`unexpected field(s): ${extra.sort().join(", ")}`);
}

function string(value: unknown, label: string): string {
  if (typeof value !== "string" || value.length === 0) throw new Error(`${label} must be a string`);
  return value;
}

function nullableString(value: unknown, label: string): string | null {
  return value === null ? null : string(value, label);
}

function number(value: unknown, label: string): number {
  if (typeof value !== "number" || !Number.isFinite(value)) throw new Error(`${label} must be a number`);
  return value;
}

function nullableNumber(value: unknown, label: string): number | null {
  return value === null ? null : number(value, label);
}

function integer(value: unknown, label: string): number {
  if (typeof value !== "number" || !Number.isInteger(value)) throw new Error(`${label} must be an integer`);
  return value;
}

function nonNegativeInteger(value: unknown, label: string): number {
  const result = integer(value, label);
  if (result < 0) throw new Error(`${label} must be a non-negative integer`);
  return result;
}

function nullableNonNegativeInteger(value: unknown, label: string): number | null {
  return value === null ? null : nonNegativeInteger(value, label);
}

function failureKind(value: unknown): FailureKind {
  const result = string(value, "failure_kind");
  if (!FAILURE_KIND_SET.has(result)) throw new Error("failure_kind must be known");
  return result as FailureKind;
}

function disposition(value: unknown): NegativeResultDisposition {
  const result = string(value, "disposition");
  if (!DISPOSITION_SET.has(result)) throw new Error("disposition must be caution, hard_ban, or noise");
  return result as NegativeResultDisposition;
}

function applicabilityScope(value: unknown): NegativeResultApplicabilityScope {
  const result = string(value, "applicability_scope");
  const matched = NEGATIVE_RESULT_APPLICABILITY_SCOPES.find((candidate) => candidate === result);
  if (!matched) throw new Error("applicability_scope must be known");
  return matched;
}

function maybeApplicabilityScope(value: unknown): NegativeResultApplicabilityScope | undefined {
  return NEGATIVE_RESULT_APPLICABILITY_SCOPES.find((candidate) => candidate === value);
}

function nullableRetestOutcome(value: unknown): NegativeResultRetestOutcome | null {
  if (value === null) return null;
  const matched = NEGATIVE_RESULT_RETEST_OUTCOMES.find((candidate) => candidate === value);
  if (!matched) throw new Error("retest_outcome must be confirmed, not_reproduced, inconclusive, or null");
  return matched;
}

function maybeRetestOutcome(value: unknown): NegativeResultRetestOutcome | null {
  return NEGATIVE_RESULT_RETEST_OUTCOMES.find((candidate) => candidate === value) ?? null;
}

function array(value: unknown, label: string): unknown[] {
  if (!Array.isArray(value)) throw new Error(`${label} must be an array`);
  return value;
}

function maybeString(value: unknown): string | undefined {
  return typeof value === "string" && value.length > 0 ? value : undefined;
}

function maybeNumber(value: unknown): number | undefined {
  return typeof value === "number" && Number.isFinite(value) ? value : undefined;
}

function nonNegativeIntegerOrNull(value: unknown): number | null {
  return typeof value === "number" && Number.isInteger(value) && value >= 0 ? value : null;
}

function stringArray(value: unknown): string[] {
  return Array.isArray(value) ? value.filter((item): item is string => typeof item === "string" && item.length > 0) : [];
}

function roundSix(value: number): number {
  const rounded = Math.sign(value) * Math.floor(Math.abs(value) * 1_000_000 + 0.5) / 1_000_000;
  return Object.is(rounded, -0) ? 0 : rounded;
}
