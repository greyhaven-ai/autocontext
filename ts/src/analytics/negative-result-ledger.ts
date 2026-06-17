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

export type FailureKind = (typeof FAILURE_KINDS)[number];
export type NegativeResultDisposition = (typeof NEGATIVE_RESULT_DISPOSITIONS)[number];

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
}

export interface FailureModeSummary {
  failure_kind: FailureKind;
  disposition: NegativeResultDisposition;
  count: number;
  result_ids: string[];
}

export interface NegativeResultLedger {
  schema_version: 1;
  run_id: string;
  generated_at: string;
  entries: NegativeResultEntry[];
  failure_mode_summary: FailureModeSummary[];
}

export interface BuildNegativeResultLedgerInput {
  runId: string;
  events: NegativeResultEventInput[];
  generatedAt?: string;
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

export function buildNegativeResultLedger(
  input: BuildNegativeResultLedgerInput,
): NegativeResultLedger {
  const entries = input.events.flatMap((event) => {
    const entry = entryFromEvent(event);
    return entry ? [entry] : [];
  });
  return parseNegativeResultLedger({
    schema_version: 1,
    run_id: input.runId,
    generated_at: input.generatedAt ?? new Date().toISOString(),
    entries,
    failure_mode_summary: failureModeSummary(entries),
  });
}

export function parseNegativeResultLedger(value: unknown): NegativeResultLedger {
  const ledger = record(value, "negative result ledger");
  exact(ledger, ["schema_version", "run_id", "generated_at", "entries", "failure_mode_summary"]);
  if (ledger.schema_version !== 1) throw new Error("schema_version must be 1");
  return {
    schema_version: 1,
    run_id: string(ledger.run_id, "run_id"),
    generated_at: string(ledger.generated_at, "generated_at"),
    entries: array(ledger.entries, "entries").map(parseEntry),
    failure_mode_summary: array(ledger.failure_mode_summary, "failure_mode_summary").map(
      parseFailureModeSummary,
    ),
  };
}

export function renderNegativeResultLessons(
  ledger: NegativeResultLedger,
  opts: { maxEntries?: number } = {},
): string {
  const rank: Record<NegativeResultDisposition, number> = { hard_ban: 0, caution: 1, noise: 2 };
  return ledger.entries
    .filter((entry) => entry.disposition !== "noise" && entry.evidence_refs.length > 0)
    .sort((left, right) => rank[left.disposition] - rank[right.disposition] || left.result_id.localeCompare(right.result_id))
    .slice(0, opts.maxEntries ?? 4)
    .map((entry) => {
      const evidence = entry.evidence_refs.slice(0, 2).map((ref) => ref.summary).join("; ");
      const delta = entry.score_delta === null ? "" : `, delta=${entry.score_delta}`;
      const prefix = entry.disposition === "hard_ban" ? "Hard ban" : "Caution";
      const suffix = entry.disposition === "hard_ban"
        ? "do not repeat without new evidence"
        : "not a ban; explore only with differentiating evidence";
      return `- ${prefix}: ${entry.failure_kind} on ${entry.branch_id} (${entry.result_id}${delta}) — ${entry.reason}; evidence: ${evidence}; ${suffix}.`;
    })
    .join("\n");
}

export function negativeResultLedgerToMarkdown(ledger: NegativeResultLedger): string {
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
    renderNegativeResultLessons(ledger) || "- None",
    "",
  ].join("\n");
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

function entryFromEvent(event: NegativeResultEventInput): NegativeResultEntry | null {
  const payload = event.payload ?? {};
  const eventType = event.event_type ?? event.event ?? "";
  const kind = eventFailureKind(payload, eventType);
  const isNegativeEvent = kind !== null || NEGATIVE_EVENTS.has(eventType);
  if (isNegativeEvent === false) return null;
  const branchId = event.branch_id ?? maybeString(payload.branch_id) ?? "";
  if (branchId.length === 0) return null;
  const eventId = event.event_id ?? (event.seq !== undefined ? `seq-${event.seq}` : "");
  const fallbackResultId = eventId || `negative-${branchId.length}`;
  const resultId = maybeString(payload.result_id) ?? fallbackResultId;
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
  };
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
  if (explicit !== undefined) return round(explicit);
  const score = maybeNumber(payload.score);
  const baseline = maybeNumber(payload.baseline_score);
  return score !== undefined && baseline !== undefined ? round(score - baseline) : null;
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

function round(value: number): number {
  return Math.round(value * 1_000_000) / 1_000_000;
}
