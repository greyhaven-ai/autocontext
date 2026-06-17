export const CAMPAIGN_TERMINAL_STATES = [
  "active",
  "completed",
  "failed",
  "budget_exhausted",
  "canceled",
] as const;
export const BRANCH_TERMINAL_STATES = [
  "pending",
  "running",
  "continued",
  "pruned",
  "succeeded",
  "failed",
  "budget_exhausted",
  "canceled",
] as const;

export type CampaignTerminalState = (typeof CAMPAIGN_TERMINAL_STATES)[number];
export type BranchTerminalState = (typeof BRANCH_TERMINAL_STATES)[number];

export interface CampaignBranchBudget {
  max_tokens: number | null;
  max_seconds: number | null;
  max_evaluations: number | null;
}

export interface CampaignBranchUsage {
  input_tokens: number;
  output_tokens: number;
  total_tokens: number;
  evaluations: number;
  runner_seconds: number;
}

export interface CampaignEvalLane {
  lane_id: string;
  label: string;
  verifier_contract_ref: string;
  seeds: string[];
  holdout_refs: string[];
  weight: number;
}

export interface CampaignBranch {
  branch_id: string;
  parent_branch_id: string | null;
  hypothesis_node_id: string | null;
  objective: string;
  budget: CampaignBranchBudget;
  usage: CampaignBranchUsage;
  terminal_state: BranchTerminalState;
  score: number | null;
  verifier_passed: boolean | null;
  terminal_reason: string;
}

export interface CampaignBranchLineageEdge {
  parent_branch_id: string;
  child_branch_id: string;
}

export interface CampaignEvidenceReference {
  uri: string;
  summary: string;
}

export interface CampaignEvidenceShareItemInput {
  share_id: string;
  from_branch_id: string;
  to_branch_ids: string[];
  summary: string;
  evidence_refs: CampaignEvidenceReference[];
}

export interface CampaignEvidenceShareItem extends CampaignEvidenceShareItemInput {
  included: boolean;
}

export interface CampaignEvidencePolicy {
  max_shared_items: number;
  max_summary_chars: number;
}

export interface CampaignEvidenceSharing {
  policy: CampaignEvidencePolicy;
  items: CampaignEvidenceShareItem[];
}

export interface CampaignBranchSummary {
  branch_count: number;
  succeeded: number;
  failed: number;
  pruned: number;
  budget_exhausted: number;
  running: number;
}

export interface CampaignRecommendation {
  branch_id: string;
  score: number | null;
  reason: string;
}

export interface CampaignLinkedReports {
  progress_report_uri: string | null;
  utilization_report_uri: string | null;
  negative_result_ledger_uri: string | null;
}

export interface CampaignModeReport {
  schema_version: 1;
  campaign_id: string;
  run_id: string;
  scenario_name: string;
  generated_at: string;
  terminal_state: CampaignTerminalState;
  branch_budget_defaults: CampaignBranchBudget;
  eval_lanes: CampaignEvalLane[];
  branches: CampaignBranch[];
  branch_lineage: CampaignBranchLineageEdge[];
  evidence_sharing: CampaignEvidenceSharing;
  branch_summary: CampaignBranchSummary;
  final_recommendation: CampaignRecommendation | null;
  linked_reports: CampaignLinkedReports;
}

export interface BuildCampaignModeReportInput {
  campaign_id: string;
  run_id: string;
  scenario_name: string;
  generated_at?: string;
  terminal_state: CampaignTerminalState;
  branch_budget_defaults: CampaignBranchBudget;
  eval_lanes: CampaignEvalLane[];
  branches: Array<Omit<CampaignBranch, "budget"> & { budget?: CampaignBranchBudget }>;
  shared_evidence: CampaignEvidenceShareItemInput[];
  linked_reports: CampaignLinkedReports;
  evidence_policy?: CampaignEvidencePolicy;
}

const CAMPAIGN_TERMINAL_STATE_SET = new Set<string>(CAMPAIGN_TERMINAL_STATES);
const BRANCH_TERMINAL_STATE_SET = new Set<string>(BRANCH_TERMINAL_STATES);

export function buildCampaignModeReport(input: BuildCampaignModeReportInput): CampaignModeReport {
  const defaults = parseBudget(input.branch_budget_defaults);
  const branches = input.branches.map((branch) => parseBranch({ ...branch, budget: branch.budget ?? defaults }));
  const policy = parseEvidencePolicy(input.evidence_policy ?? { max_shared_items: 2, max_summary_chars: 240 });
  return parseCampaignModeReport({
    schema_version: 1,
    campaign_id: input.campaign_id,
    run_id: input.run_id,
    scenario_name: input.scenario_name,
    generated_at: input.generated_at ?? new Date().toISOString(),
    terminal_state: input.terminal_state,
    branch_budget_defaults: defaults,
    eval_lanes: input.eval_lanes,
    branches,
    branch_lineage: branchLineage(branches),
    evidence_sharing: { policy, items: evidenceItems(input.shared_evidence, policy) },
    branch_summary: branchSummary(branches),
    final_recommendation: recommendation(branches),
    linked_reports: input.linked_reports,
  });
}

export function parseCampaignModeReport(value: unknown): CampaignModeReport {
  const report = record(value, "campaign mode report");
  exact(report, [
    "schema_version",
    "campaign_id",
    "run_id",
    "scenario_name",
    "generated_at",
    "terminal_state",
    "branch_budget_defaults",
    "eval_lanes",
    "branches",
    "branch_lineage",
    "evidence_sharing",
    "branch_summary",
    "final_recommendation",
    "linked_reports",
  ]);
  if (report.schema_version !== 1) throw new Error("schema_version must be 1");
  return {
    schema_version: 1,
    campaign_id: string(report.campaign_id, "campaign_id"),
    run_id: string(report.run_id, "run_id"),
    scenario_name: string(report.scenario_name, "scenario_name"),
    generated_at: string(report.generated_at, "generated_at"),
    terminal_state: campaignTerminalState(report.terminal_state),
    branch_budget_defaults: parseBudget(report.branch_budget_defaults),
    eval_lanes: array(report.eval_lanes, "eval_lanes").map(parseEvalLane),
    branches: array(report.branches, "branches").map(parseBranch),
    branch_lineage: array(report.branch_lineage, "branch_lineage").map(parseLineage),
    evidence_sharing: parseEvidenceSharing(report.evidence_sharing),
    branch_summary: parseBranchSummary(report.branch_summary),
    final_recommendation: report.final_recommendation === null ? null : parseRecommendation(report.final_recommendation),
    linked_reports: parseLinkedReports(report.linked_reports),
  };
}

export function renderCampaignEvidenceShare(report: CampaignModeReport): string {
  return report.evidence_sharing.items
    .filter((item) => item.included)
    .map((item) => {
      const targets = item.to_branch_ids.length ? item.to_branch_ids.join(", ") : "all branches";
      const evidence = item.evidence_refs.slice(0, 2).map((ref) => ref.summary).join("; ");
      return `- ${item.share_id}: ${item.from_branch_id} -> ${targets}: ${item.summary}; evidence: ${evidence}`;
    })
    .join("\n");
}

export function campaignModeReportToMarkdown(report: CampaignModeReport): string {
  const recommendation = report.final_recommendation
    ? `- Recommendation: ${report.final_recommendation.branch_id} (score=${report.final_recommendation.score}) — ${report.final_recommendation.reason}`
    : "- Recommendation: none";
  return [
    `# Campaign Mode Report: ${report.campaign_id}`,
    `- Run: ${report.run_id}`,
    `- Scenario: ${report.scenario_name}`,
    `- Terminal state: ${report.terminal_state}`,
    `- Branches: ${report.branch_summary.branch_count}`,
    recommendation,
    "",
    "## Shared Evidence",
    renderCampaignEvidenceShare(report) || "- None",
    "",
  ].join("\n");
}

function parseBudget(value: unknown): CampaignBranchBudget {
  const item = record(value, "branch budget");
  exact(item, ["max_tokens", "max_seconds", "max_evaluations"]);
  return {
    max_tokens: nullableNonNegativeInteger(item.max_tokens, "max_tokens"),
    max_seconds: nullableNonNegativeNumber(item.max_seconds, "max_seconds"),
    max_evaluations: nullableNonNegativeInteger(item.max_evaluations, "max_evaluations"),
  };
}

function parseUsage(value: unknown): CampaignBranchUsage {
  const item = record(value, "branch usage");
  exact(item, ["input_tokens", "output_tokens", "total_tokens", "evaluations", "runner_seconds"]);
  return {
    input_tokens: nonNegativeInteger(item.input_tokens, "input_tokens"),
    output_tokens: nonNegativeInteger(item.output_tokens, "output_tokens"),
    total_tokens: nonNegativeInteger(item.total_tokens, "total_tokens"),
    evaluations: nonNegativeInteger(item.evaluations, "evaluations"),
    runner_seconds: nonNegativeNumber(item.runner_seconds, "runner_seconds"),
  };
}

function parseEvalLane(value: unknown): CampaignEvalLane {
  const item = record(value, "eval lane");
  exact(item, ["lane_id", "label", "verifier_contract_ref", "seeds", "holdout_refs", "weight"]);
  return {
    lane_id: string(item.lane_id, "lane_id"),
    label: string(item.label, "label"),
    verifier_contract_ref: string(item.verifier_contract_ref, "verifier_contract_ref"),
    seeds: stringArray(item.seeds, "seeds"),
    holdout_refs: stringArray(item.holdout_refs, "holdout_refs"),
    weight: nonNegativeNumber(item.weight, "weight"),
  };
}

function parseBranch(value: unknown): CampaignBranch {
  const item = record(value, "campaign branch");
  exact(item, [
    "branch_id",
    "parent_branch_id",
    "hypothesis_node_id",
    "objective",
    "budget",
    "usage",
    "terminal_state",
    "score",
    "verifier_passed",
    "terminal_reason",
  ]);
  return {
    branch_id: string(item.branch_id, "branch_id"),
    parent_branch_id: nullableString(item.parent_branch_id, "parent_branch_id"),
    hypothesis_node_id: nullableString(item.hypothesis_node_id, "hypothesis_node_id"),
    objective: string(item.objective, "objective"),
    budget: parseBudget(item.budget),
    usage: parseUsage(item.usage),
    terminal_state: branchTerminalState(item.terminal_state),
    score: nullableNumber(item.score, "score"),
    verifier_passed: nullableBoolean(item.verifier_passed, "verifier_passed"),
    terminal_reason: string(item.terminal_reason, "terminal_reason"),
  };
}

function parseLineage(value: unknown): CampaignBranchLineageEdge {
  const item = record(value, "branch lineage edge");
  exact(item, ["parent_branch_id", "child_branch_id"]);
  return {
    parent_branch_id: string(item.parent_branch_id, "parent_branch_id"),
    child_branch_id: string(item.child_branch_id, "child_branch_id"),
  };
}

function parseEvidenceReference(value: unknown): CampaignEvidenceReference {
  const item = record(value, "evidence reference");
  exact(item, ["uri", "summary"]);
  return { uri: string(item.uri, "uri"), summary: string(item.summary, "summary") };
}

function parseEvidenceItem(value: unknown): CampaignEvidenceShareItem {
  const item = record(value, "evidence share item");
  exact(item, ["share_id", "from_branch_id", "to_branch_ids", "summary", "included", "evidence_refs"]);
  return {
    share_id: string(item.share_id, "share_id"),
    from_branch_id: string(item.from_branch_id, "from_branch_id"),
    to_branch_ids: stringArray(item.to_branch_ids, "to_branch_ids"),
    summary: string(item.summary, "summary"),
    included: boolean(item.included, "included"),
    evidence_refs: array(item.evidence_refs, "evidence_refs").map(parseEvidenceReference),
  };
}

function parseEvidencePolicy(value: unknown): CampaignEvidencePolicy {
  const item = record(value, "evidence policy");
  exact(item, ["max_shared_items", "max_summary_chars"]);
  return {
    max_shared_items: nonNegativeInteger(item.max_shared_items, "max_shared_items"),
    max_summary_chars: positiveInteger(item.max_summary_chars, "max_summary_chars"),
  };
}

function parseEvidenceSharing(value: unknown): CampaignEvidenceSharing {
  const item = record(value, "evidence sharing");
  exact(item, ["policy", "items"]);
  return {
    policy: parseEvidencePolicy(item.policy),
    items: array(item.items, "items").map(parseEvidenceItem),
  };
}

function parseBranchSummary(value: unknown): CampaignBranchSummary {
  const item = record(value, "branch summary");
  exact(item, ["branch_count", "succeeded", "failed", "pruned", "budget_exhausted", "running"]);
  return {
    branch_count: nonNegativeInteger(item.branch_count, "branch_count"),
    succeeded: nonNegativeInteger(item.succeeded, "succeeded"),
    failed: nonNegativeInteger(item.failed, "failed"),
    pruned: nonNegativeInteger(item.pruned, "pruned"),
    budget_exhausted: nonNegativeInteger(item.budget_exhausted, "budget_exhausted"),
    running: nonNegativeInteger(item.running, "running"),
  };
}

function parseRecommendation(value: unknown): CampaignRecommendation {
  const item = record(value, "final recommendation");
  exact(item, ["branch_id", "score", "reason"]);
  return {
    branch_id: string(item.branch_id, "branch_id"),
    score: nullableNumber(item.score, "score"),
    reason: string(item.reason, "reason"),
  };
}

function parseLinkedReports(value: unknown): CampaignLinkedReports {
  const item = record(value, "linked reports");
  exact(item, ["progress_report_uri", "utilization_report_uri", "negative_result_ledger_uri"]);
  return {
    progress_report_uri: nullableString(item.progress_report_uri, "progress_report_uri"),
    utilization_report_uri: nullableString(item.utilization_report_uri, "utilization_report_uri"),
    negative_result_ledger_uri: nullableString(item.negative_result_ledger_uri, "negative_result_ledger_uri"),
  };
}

function branchLineage(branches: CampaignBranch[]): CampaignBranchLineageEdge[] {
  return branches.flatMap((branch) =>
    branch.parent_branch_id ? [{ parent_branch_id: branch.parent_branch_id, child_branch_id: branch.branch_id }] : [],
  );
}

function branchSummary(branches: CampaignBranch[]): CampaignBranchSummary {
  const active = new Set<BranchTerminalState>(["pending", "running", "continued"]);
  return {
    branch_count: branches.length,
    succeeded: branches.filter((branch) => branch.terminal_state === "succeeded").length,
    failed: branches.filter((branch) => branch.terminal_state === "failed").length,
    pruned: branches.filter((branch) => branch.terminal_state === "pruned").length,
    budget_exhausted: branches.filter((branch) => branch.terminal_state === "budget_exhausted").length,
    running: branches.filter((branch) => active.has(branch.terminal_state)).length,
  };
}

function recommendation(branches: CampaignBranch[]): CampaignRecommendation | null {
  let eligible = branches.filter((branch) => branch.score !== null && branch.verifier_passed === true);
  if (!eligible.length) eligible = branches.filter((branch) => branch.score !== null);
  const best = eligible.sort((left, right) => (right.score ?? -Infinity) - (left.score ?? -Infinity))[0];
  return best ? { branch_id: best.branch_id, score: best.score, reason: best.terminal_reason } : null;
}

function evidenceItems(
  items: CampaignEvidenceShareItemInput[],
  policy: CampaignEvidencePolicy,
): CampaignEvidenceShareItem[] {
  let includedCount = 0;
  return items.map((item) => {
    const included = item.evidence_refs.length > 0 && includedCount < policy.max_shared_items;
    if (included) includedCount += 1;
    return parseEvidenceItem({
      ...item,
      summary: truncate(item.summary, policy.max_summary_chars),
      included,
    });
  });
}

function truncate(value: string, maxChars: number): string {
  return value.length <= maxChars ? value : value.slice(0, maxChars - 1).trimEnd() + "…";
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

function nonNegativeNumber(value: unknown, label: string): number {
  const result = number(value, label);
  if (result < 0) throw new Error(`${label} must be non-negative`);
  return result;
}

function nullableNonNegativeNumber(value: unknown, label: string): number | null {
  return value === null ? null : nonNegativeNumber(value, label);
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

function positiveInteger(value: unknown, label: string): number {
  const result = integer(value, label);
  if (result < 1) throw new Error(`${label} must be a positive integer`);
  return result;
}

function boolean(value: unknown, label: string): boolean {
  if (typeof value !== "boolean") throw new Error(`${label} must be a boolean`);
  return value;
}

function nullableBoolean(value: unknown, label: string): boolean | null {
  return value === null ? null : boolean(value, label);
}

function array(value: unknown, label: string): unknown[] {
  if (!Array.isArray(value)) throw new Error(`${label} must be an array`);
  return value;
}

function stringArray(value: unknown, label: string): string[] {
  return array(value, label).map((item) => string(item, label));
}

function campaignTerminalState(value: unknown): CampaignTerminalState {
  const result = string(value, "terminal_state");
  if (!CAMPAIGN_TERMINAL_STATE_SET.has(result)) throw new Error("terminal_state must be a campaign terminal state");
  return result as CampaignTerminalState;
}

function branchTerminalState(value: unknown): BranchTerminalState {
  const result = string(value, "terminal_state");
  if (!BRANCH_TERMINAL_STATE_SET.has(result)) throw new Error("terminal_state must be a branch terminal state");
  return result as BranchTerminalState;
}
