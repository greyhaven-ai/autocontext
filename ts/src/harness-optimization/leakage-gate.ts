import type { LeakageAudit } from "./leakage.js";

/**
 * Verified/exploratory leakage gate (AC-879).
 *
 * TypeScript half of the AC-879 parity pair: the same three-branch gate lives
 * here and in
 * `autocontext/src/autocontext/harness_optimization/leakage_gate.py`, and the
 * shared fixture
 * (`fixtures/harness-optimization/leakage-cases/leakage-cases.json`) proves
 * both languages reach identical advance / non_promotion_grade decisions.
 *
 * Consumes a LeakageAudit and the run mode. Verified runs fail closed on
 * contaminated or unknown status, or on missing prompt provenance. Exploratory
 * runs always advance but are stamped non-promotion-grade. Caller-gated: this
 * function never reads settings, so default runs are unaffected until a caller
 * invokes it.
 */

export interface LeakageGateDecision {
  advance: boolean;
  non_promotion_grade: boolean;
  rationale: string;
}

export function evaluateLeakageGate(
  audit: LeakageAudit,
  mode: string,
  promptProvenance: string,
): LeakageGateDecision {
  if (mode === "exploratory") {
    return {
      advance: true,
      non_promotion_grade: true,
      rationale: "exploratory override: advancing non-promotion-grade regardless of leakage",
    };
  }
  // verified
  const blockers: string[] = [];
  if (audit.status !== "clean") {
    blockers.push(`leakage status ${audit.status}: ${audit.reasons.join("; ")}`);
  }
  if (promptProvenance.trim() === "") {
    blockers.push("missing prompt provenance");
  }
  if (blockers.length > 0) {
    return {
      advance: false,
      non_promotion_grade: true,
      rationale: "verified run blocked: " + blockers.join("; "),
    };
  }
  return { advance: true, non_promotion_grade: false, rationale: "verified run clean" };
}
