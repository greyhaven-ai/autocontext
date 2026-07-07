/* eslint-disable */
// AUTO-GENERATED from src/harness-optimization/contract/json-schemas/ — DO NOT EDIT.
// Regenerate with: node scripts/generate-harness-optimization-types.mjs
// CI gate: node scripts/generate-harness-optimization-types.mjs --check

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
