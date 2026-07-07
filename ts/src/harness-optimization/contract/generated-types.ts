/* eslint-disable */
// AUTO-GENERATED from src/harness-optimization/contract/json-schemas/ — DO NOT EDIT.
// Regenerate with: node scripts/generate-harness-optimization-types.mjs
// CI gate: node scripts/generate-harness-optimization-types.mjs --check

// ---- _aggregate.schema.json ----
/**
 * Codegen-only aggregate root; $refs each artifact so one models.py is emitted. Not a runtime schema.
 */
export interface HarnessOptimizationContracts {
  [k: string]: unknown;
}

// ---- calibration-report.schema.json ----
/**
 * Noise calibration for a harness score series (AC-881). Summarises a scenario's score samples (mean, variance, standard error) and recommends a promotion margin and trial count so the configured gate sits above measurement noise. Flags when a sparse metric is too noisy to gate on. Shared source of truth for both the Python and TypeScript autocontext packages.
 */
export interface CalibrationReport {
  /**
   * Schema version for forward compatibility. Always 1 for this revision.
   */
  schema_version: 1;
  /**
   * Scenario or family the score series came from.
   */
  scenario_id: string;
  /**
   * Number of score samples in the series (n).
   */
  sample_size: number;
  /**
   * Mean of the score series.
   */
  mean: number;
  /**
   * Sample variance (ddof=1); 0 when n<2.
   */
  variance: number;
  /**
   * Standard deviation, sqrt of the variance.
   */
  std_dev: number;
  /**
   * Standard error, std_dev over sqrt(n); 0 when n<2.
   */
  standard_error: number;
  /**
   * Recommended margin: noise_multiplier times standard_error.
   */
  recommended_min_delta: number;
  /**
   * Trials so the mean SE falls under current_min_delta, capped by budget.
   */
  recommended_trial_count: number;
  /**
   * The promotion margin currently configured.
   */
  current_min_delta: number;
  /**
   * Whether the current margin sits above or below the noise floor.
   */
  margin_vs_noise: "above_noise" | "below_noise";
  /**
   * True when the sparse metric is too noisy to gate on.
   */
  sparse_metric_too_noisy: boolean;
  /**
   * Optional human-readable rationale for audit.
   */
  notes?: string;
}

// ---- candidate-evidence.schema.json ----
/**
 * Canonical evidence record for a single harness-optimization candidate (AC-876). A candidate is a proposed mechanism change to some target surface, carrying the hypothesis, the concrete changes, the fix and regression cases it is expected to move, its cost expectation, and its cross-language parity status. This schema is the single source of truth for both the Python and TypeScript autocontext packages.
 */
export interface CandidateEvidence {
  /**
   * Schema version for forward compatibility. Always 1 for this revision.
   */
  schema_version: 1;
  /**
   * Stable unique identifier for this candidate.
   */
  candidate_id: string;
  /**
   * Identifier of the frontier this candidate descends from. May be empty for a root candidate.
   */
  parent_frontier_id?: string;
  /**
   * Human-readable name of the mechanism this candidate changes.
   */
  mechanism_name: string;
  /**
   * Category of mechanism being changed.
   */
  mechanism_type:
    "deterministic_code" | "prompt_playbook" | "tool_wrapper" | "context_policy" | "judge_policy" | "mixed";
  /**
   * The surface of the harness this candidate targets.
   */
  target_surface:
    "prompt" | "tool" | "harness_validator" | "runtime_adapter" | "artifact_landing" | "evaluator" | "routing" | "docs";
  /**
   * The falsifiable claim about why this change should help.
   */
  hypothesis: string;
  /**
   * Description of the concrete changes this candidate makes.
   */
  changes: string;
  /**
   * Paths or identifiers of the artifacts this candidate modifies.
   */
  changed_artifacts?: string[];
  /**
   * Named seeds, tasks, or traces this candidate is expected to improve.
   */
  fix_cases?: string[];
  /**
   * Named seeds, tasks, or traces this candidate is expected to keep flat.
   */
  regression_cases?: string[];
  /**
   * Smoke, replay, or dry-run evidence gathered so far. May be empty at proposal time.
   */
  observed?: string;
  /**
   * How this candidate will be validated before promotion.
   */
  validation_plan: string;
  /**
   * Expected marginal cost of adopting this candidate.
   */
  cost_expectation?: {
    /**
     * Expected additional tokens per run.
     */
    extra_tokens?: number;
    /**
     * Expected additional model or tool calls per run.
     */
    extra_calls?: number;
    /**
     * Expected additional wall-clock seconds per run.
     */
    extra_seconds?: number;
  };
  /**
   * What data the proposer was allowed to inspect, for leakage auditing.
   */
  leakage_scope?: string[];
  /**
   * Cross-language parity status for this candidate.
   */
  parity: {
    /**
     * Implementation status of this candidate in the Python package.
     */
    python: "implemented" | "pending" | "n_a";
    /**
     * Implementation status of this candidate in the TypeScript package.
     */
    typescript: "implemented" | "pending" | "n_a";
    /**
     * Content hash of the shared schema the two implementations agree on.
     */
    schema_hash: string;
  };
}

// ---- frontier-mechanism.schema.json ----
/**
 * A promoted mechanism on the harness-optimization frontier (AC-880). It records a candidate that passed the promotion gate, its lineage parent, the surfaces it affects, the regression risks it carries, and how many generations of support it has. This is the archived record of what advanced. Shared source of truth for both the Python and TypeScript autocontext packages.
 */
export interface FrontierMechanism {
  /**
   * Schema version for forward compatibility. Always 1 for this revision.
   */
  schema_version: 1;
  /**
   * Stable unique identifier for this frontier mechanism.
   */
  mechanism_id: string;
  /**
   * Identifier of the candidate evidence record this mechanism was promoted from.
   */
  candidate_evidence_id: string;
  /**
   * Identifier of the frontier this mechanism descends from. Empty for a root frontier.
   */
  parent_frontier_id?: string;
  /**
   * Human-readable name of the promoted mechanism.
   */
  mechanism_name: string;
  /**
   * Category of mechanism being changed.
   */
  mechanism_type:
    "deterministic_code" | "prompt_playbook" | "tool_wrapper" | "context_policy" | "judge_policy" | "mixed";
  /**
   * The surface of the harness this mechanism targets.
   */
  target_surface:
    "prompt" | "tool" | "harness_validator" | "runtime_adapter" | "artifact_landing" | "evaluator" | "routing" | "docs";
  /**
   * The advancement decision that promoted this mechanism.
   */
  gate_decision: string;
  /**
   * Surfaces this mechanism touches beyond its primary target.
   */
  affected_surfaces: string[];
  /**
   * Known regression risks this mechanism carries forward.
   */
  regression_risks: string[];
  /**
   * Number of runs or generations that support this mechanism.
   */
  support_count: number;
  /**
   * Generation index at which this mechanism was promoted.
   */
  promoted_at_generation: number;
}

// ---- integrity-metadata.schema.json ----
/**
 * Declared integrity scope for a harness-optimization run (AC-879). It records which sources the proposer and evaluator were allowed to read, which are forbidden (holdout, test splits), the web-access policy, the benchmark split manifest in play, and the computed leakage status so a reviewer can prove a candidate was proposed without touching held-out evidence. Verified-mode enforcement (fail closed on contamination or unknown status) is applied by the leakage gate, not by this schema. This schema is the single source of truth for both the Python and TypeScript autocontext packages.
 */
export interface IntegrityMetadata {
  /**
   * Schema version for forward compatibility. Always 1 for this revision.
   */
  schema_version: 1;
  /**
   * Identifier of the run this integrity record describes.
   */
  run_id: string;
  /**
   * Run mode: verified fails closed on leakage, exploratory is marked non-promotion-grade.
   */
  mode: "verified" | "exploratory";
  /**
   * Source ids the proposer or evaluator may read.
   */
  allowed_sources: string[];
  /**
   * Source ids that must never be read, for example holdout or test-split sources.
   */
  forbidden_sources: string[];
  /**
   * Subset of sources whose status must be known-clean for a verified run to advance.
   */
  required_sources: string[];
  /**
   * Web-access policy: blocked forbids all web reads, allowlist permits only listed hosts, open permits any.
   */
  web_policy: "blocked" | "allowlist" | "open";
  /**
   * Hosts permitted when web_policy is allowlist. Optional; omitted or empty means no host is permitted.
   */
  web_allowlist?: string[] | null;
  /**
   * Benchmark or test split manifest ids in play for this run.
   */
  split_ids: string[];
  /**
   * Where proposer prompts came from. Verified mode requires it non-empty (gate-enforced, not schema).
   */
  prompt_provenance?: string | null;
  /**
   * What the runtime or adapter can enforce, for example filesystem sandboxing or network blocking.
   */
  adapter_capabilities: string[];
  /**
   * Computed leakage status: clean, contaminated, or unknown when it cannot be proven clean.
   */
  leakage_status: "clean" | "contaminated" | "unknown";
  /**
   * Human-readable reasons for a contaminated or unknown status. Empty when clean.
   */
  contamination_reasons: string[];
}

// ---- orphan-mechanism.schema.json ----
/**
 * A rejected or rolled-back mechanism in the harness-optimization archive (AC-880). It records a candidate that failed the promotion gate, the failure family it belongs to, why it was rejected, how many times it was retried, and whether a later combination rescued it. This is the archived record of what did not advance. Shared source of truth for both the Python and TypeScript autocontext packages.
 */
export interface OrphanMechanism {
  /**
   * Schema version for forward compatibility. Always 1 for this revision.
   */
  schema_version: 1;
  /**
   * Stable unique identifier for this orphan mechanism.
   */
  mechanism_id: string;
  /**
   * Identifier of the candidate evidence record this mechanism came from.
   */
  candidate_evidence_id: string;
  /**
   * Identifier of the frontier this mechanism descends from. Empty for a root candidate.
   */
  parent_frontier_id?: string;
  /**
   * Human-readable name of the orphaned mechanism.
   */
  mechanism_name: string;
  /**
   * Category of mechanism being changed.
   */
  mechanism_type:
    "deterministic_code" | "prompt_playbook" | "tool_wrapper" | "context_policy" | "judge_policy" | "mixed";
  /**
   * The surface of the harness this mechanism targets.
   */
  target_surface:
    "prompt" | "tool" | "harness_validator" | "runtime_adapter" | "artifact_landing" | "evaluator" | "routing" | "docs";
  /**
   * The gate outcome for this mechanism, such as retry, rollback, or reject.
   */
  gate_decision: string;
  /**
   * The family of failure this mechanism belongs to, for clustering orphans.
   */
  failure_family: string;
  /**
   * Human-readable reason this mechanism was not promoted.
   */
  rejection_reason: string;
  /**
   * Number of times this mechanism was retried before being orphaned.
   */
  retry_count: number;
  /**
   * Number of runs or generations that support this mechanism.
   */
  support_count?: number;
  /**
   * Frontier id a later combination rescued this into. Empty while still orphaned.
   */
  rescued_into_frontier_id?: string;
}

// ---- promotion-score.schema.json ----
/**
 * Computed harness-promotion score for a single candidate (AC-877). Combines the dense quality signal with the sparse per-case success rate, marginal token cost, error rate, and score variance under a named, versioned set of weights. This is the artifact the promotion gate reads to decide whether a candidate advances. Shared source of truth for both the Python and TypeScript autocontext packages.
 */
export interface PromotionScore {
  /**
   * Schema version for forward compatibility. Always 1 for this revision.
   */
  schema_version: 1;
  /**
   * Stable unique identifier of the candidate this score belongs to.
   */
  candidate_id: string;
  /**
   * Version tag of the weight set used to compute this score.
   */
  weight_version: string;
  /**
   * The measured components that feed the weighted promotion score.
   */
  components: {
    /**
     * Dense quality signal, typically a judge or rubric score.
     */
    dense_quality_score: number;
    /**
     * Fraction of target cases the candidate resolved, in [0, 1].
     */
    sparse_success_rate: number;
    /**
     * Marginal token cost expressed per million tokens.
     */
    tokens_per_million: number;
    /**
     * Fraction of runs that errored, in [0, 1].
     */
    error_rate: number;
    /**
     * Variance of the score across samples.
     */
    score_variance: number;
  };
  /**
   * The named weights applied to each component.
   */
  weights: {
    /**
     * Weight applied to the sparse success rate.
     */
    sparse_success_weight: number;
    /**
     * Weight applied to the marginal token cost.
     */
    token_cost_weight: number;
    /**
     * Weight applied to the error rate.
     */
    error_weight: number;
    /**
     * Weight applied to the score variance.
     */
    variance_weight: number;
  };
  /**
   * The computed harness promotion score.
   */
  score: number;
  /**
   * Cross-language parity status for this candidate.
   */
  parity: {
    /**
     * Implementation status of this candidate in the Python package.
     */
    python: "implemented" | "pending" | "n_a";
    /**
     * Implementation status of this candidate in the TypeScript package.
     */
    typescript: "implemented" | "pending" | "n_a";
    /**
     * Content hash of the shared schema the two implementations agree on.
     */
    schema_hash: string;
  };
}

// ---- repair-result.schema.json ----
/**
 * Outcome of a single deterministic repair applied (or not applied) to a harness surface (AC-878). A repair is a named, auditable mechanism that fixes a known failure mode (for example tool_call_json, artifact_landing, finish_guard, loop_guard); this record captures whether it fired, why, what it targeted, and the before/after metadata a reviewer needs to trust the gate. This schema is the single source of truth for both the Python and TypeScript autocontext packages.
 */
export interface RepairResult {
  /**
   * Schema version for forward compatibility. Always 1 for this revision.
   */
  schema_version: 1;
  /**
   * Human-readable name of the repair mechanism, e.g. tool_call_json or finish_guard.
   */
  repair_name: string;
  /**
   * Whether the repair fired: applied, skipped, or not_applicable to this input.
   */
  status: "applied" | "skipped" | "not_applicable";
  /**
   * Human-auditable explanation of why the repair was applied or skipped.
   */
  reason: string;
  /**
   * What was repaired: a path, a tool name, or empty when nothing was targeted.
   */
  target?: string;
  /**
   * Pre-repair metadata, e.g. {"valid": false}.
   */
  before?: {
    [k: string]: unknown;
  };
  /**
   * Post-repair metadata, e.g. {"valid": true}.
   */
  after?: {
    [k: string]: unknown;
  };
  /**
   * Cross-language parity status for this candidate.
   */
  parity: {
    /**
     * Implementation status of this candidate in the Python package.
     */
    python: "implemented" | "pending" | "n_a";
    /**
     * Implementation status of this candidate in the TypeScript package.
     */
    typescript: "implemented" | "pending" | "n_a";
    /**
     * Content hash of the shared schema the two implementations agree on.
     */
    schema_hash: string;
  };
}
