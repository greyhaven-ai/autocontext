import {
  TraceFindingReportSchema,
  type FailureMotif,
  type TraceFinding,
  type TraceFindingReport,
} from "./trace-findings.js";
import type { HarnessChangeProposal } from "../control-plane/contract/types.js";

export type TraceGateOperatorState =
  | "missing_report"
  | "incomplete_analysis"
  | "no_findings"
  | "ready";
export type TraceGateAnalysisState = "complete" | "incomplete";
export type TraceEvidenceLinkKind = "trace_message" | "artifact";

export interface TraceEvidenceLink {
  readonly kind: TraceEvidenceLinkKind;
  readonly ref: string;
  readonly label: string;
  readonly href: string;
}

export interface TraceGateReportSummary {
  readonly report_id: string;
  readonly trace_id: string;
  readonly source_harness: string;
  readonly created_at: string;
  readonly finding_count: number;
  readonly failure_mode_count: number;
}

export interface TraceGateFindingView {
  readonly finding_id: string;
  readonly category: string;
  readonly severity: string;
  readonly title: string;
  readonly description: string;
  readonly evidence_count: number;
  readonly evidence_refs: readonly TraceEvidenceLink[];
  readonly linked_proposal_ids: readonly string[];
}

export interface TraceGateFailureModeView {
  readonly motif_id: string;
  readonly category: string;
  readonly occurrence_count: number;
  readonly description: string;
  readonly representative_evidence_refs: readonly TraceEvidenceLink[];
}

export interface TraceGateProposalView {
  readonly proposal_id: string;
  readonly status: string;
  readonly target_surface: string;
  readonly summary: string;
  readonly finding_ids: readonly string[];
  readonly patch_count: number;
  readonly rollback_criteria: readonly string[];
  readonly gate_status: string;
  readonly gate_reason: string;
}

export interface TraceGateDecisionView {
  readonly proposal_id: string;
  readonly status: string;
  readonly reason: string;
  readonly validation_mode: string;
  readonly suite_id: string;
  readonly decided_at: string;
  readonly evidence_refs: readonly TraceEvidenceLink[];
}

export interface TraceGateOperatorView {
  readonly schema_version: "1";
  readonly run_id: string;
  readonly state: TraceGateOperatorState;
  readonly summary: string;
  readonly report: TraceGateReportSummary | null;
  readonly findings: readonly TraceGateFindingView[];
  readonly failure_modes: readonly TraceGateFailureModeView[];
  readonly proposals: readonly TraceGateProposalView[];
  readonly gate_decisions: readonly TraceGateDecisionView[];
}

export interface BuildTraceGateOperatorViewInput {
  readonly run_id: string;
  readonly report?: TraceFindingReport | null;
  readonly proposals?: readonly HarnessChangeProposal[];
  readonly analysis_state?: TraceGateAnalysisState;
}

export function buildTraceGateOperatorView(
  input: BuildTraceGateOperatorViewInput,
): TraceGateOperatorView {
  const proposals = input.proposals ?? [];
  const report = input.report ? TraceFindingReportSchema.parse(input.report) : null;
  const state = resolveState(report, proposals, input.analysis_state ?? "complete");
  const linkedProposalIds = proposalIdsByFinding(proposals);
  return {
    schema_version: "1",
    run_id: input.run_id,
    state,
    summary: summaryForState(state, report),
    report: report ? summarizeReport(report) : null,
    findings: report?.findings.map((finding) => findingView(finding, linkedProposalIds)) ?? [],
    failure_modes: report?.failureMotifs.map(failureModeView) ?? [],
    proposals: proposals.map(proposalView),
    gate_decisions: proposals.flatMap((proposal) => decisionView(proposal)),
  };
}

export function renderTraceGateOperatorViewLines(view: TraceGateOperatorView): string[] {
  const lines = [`Trace gates for ${view.run_id} — ${view.state}`, view.summary];
  lines.push("Findings:");
  if (view.findings.length === 0) {
    lines.push("- none");
  } else {
    for (const finding of view.findings) {
      lines.push(
        `- [${finding.severity}/${finding.category}] ${finding.title} evidence=${formatEvidenceLabels(finding.evidence_refs)} proposals=${formatList(finding.linked_proposal_ids)}`,
      );
    }
  }

  lines.push("Recurring failure modes:");
  if (view.failure_modes.length === 0) {
    lines.push("- none");
  } else {
    for (const mode of view.failure_modes) {
      lines.push(
        `- ${mode.category} x${mode.occurrence_count} evidence=${formatEvidenceLabels(mode.representative_evidence_refs)}`,
      );
    }
  }

  lines.push("Proposals:");
  if (view.proposals.length === 0) {
    lines.push("- none");
  } else {
    for (const proposal of view.proposals) {
      lines.push(
        `- ${proposal.proposal_id} ${proposal.status} target=${proposal.target_surface} patches=${proposal.patch_count} — ${proposal.summary}`,
      );
    }
  }

  lines.push("Gate decisions:");
  if (view.gate_decisions.length === 0) {
    lines.push("- none");
  } else {
    for (const decision of view.gate_decisions) {
      lines.push(
        `- ${decision.proposal_id} ${decision.status} ${decision.validation_mode || "unknown"} — ${decision.reason}`,
      );
    }
  }
  return lines;
}

function resolveState(
  report: TraceFindingReport | null,
  proposals: readonly HarnessChangeProposal[],
  analysisState: TraceGateAnalysisState,
): TraceGateOperatorState {
  if (analysisState === "incomplete") {
    return "incomplete_analysis";
  }
  if (!report) {
    return "missing_report";
  }
  if (report.findings.length === 0 && proposals.length === 0) {
    return "no_findings";
  }
  return "ready";
}

function summaryForState(state: TraceGateOperatorState, report: TraceFindingReport | null): string {
  switch (state) {
    case "incomplete_analysis":
      return "Trace analysis is still running; findings and gate decisions may be incomplete.";
    case "missing_report":
      return "No trace finding report is available for this run yet.";
    case "no_findings":
      return report?.summary ?? "No findings or proposals are available for this run.";
    case "ready":
      return report?.summary ?? "Trace findings and gate decisions are ready.";
  }
}

function summarizeReport(report: TraceFindingReport): TraceGateReportSummary {
  return {
    report_id: report.reportId,
    trace_id: report.traceId,
    source_harness: report.sourceHarness,
    created_at: report.createdAt,
    finding_count: report.findings.length,
    failure_mode_count: report.failureMotifs.length,
  };
}

function findingView(
  finding: TraceFinding,
  linkedProposalIds: ReadonlyMap<string, readonly string[]>,
): TraceGateFindingView {
  const evidenceRefs = finding.evidenceMessageIndexes.map(traceMessageEvidenceLink);
  return {
    finding_id: finding.findingId,
    category: finding.category,
    severity: finding.severity,
    title: finding.title,
    description: finding.description,
    evidence_count: evidenceRefs.length,
    evidence_refs: evidenceRefs,
    linked_proposal_ids: linkedProposalIds.get(finding.findingId) ?? [],
  };
}

function failureModeView(motif: FailureMotif): TraceGateFailureModeView {
  return {
    motif_id: motif.motifId,
    category: motif.category,
    occurrence_count: motif.occurrenceCount,
    description: motif.description,
    representative_evidence_refs: motif.evidenceMessageIndexes.map(traceMessageEvidenceLink),
  };
}

function proposalView(proposal: HarnessChangeProposal): TraceGateProposalView {
  return {
    proposal_id: proposal.id,
    status: proposal.status,
    target_surface: proposal.targetSurface,
    summary: proposal.proposedEdit.summary,
    finding_ids: proposal.findingIds,
    patch_count: proposal.proposedEdit.patches.length,
    rollback_criteria: proposal.rollbackCriteria,
    gate_status: proposal.decision?.status ?? "",
    gate_reason: proposal.decision?.reason ?? "",
  };
}

function decisionView(proposal: HarnessChangeProposal): TraceGateDecisionView[] {
  if (!proposal.decision) {
    return [];
  }
  return [
    {
      proposal_id: proposal.id,
      status: proposal.decision.status,
      reason: proposal.decision.reason,
      validation_mode: proposal.decision.validation.mode,
      suite_id: proposal.decision.validation.suiteId,
      decided_at: proposal.decision.decidedAt,
      evidence_refs: proposal.decision.validation.evidenceRefs.map(artifactEvidenceLink),
    },
  ];
}

function proposalIdsByFinding(
  proposals: readonly HarnessChangeProposal[],
): ReadonlyMap<string, readonly string[]> {
  const ids = new Map<string, string[]>();
  for (const proposal of proposals) {
    for (const findingId of proposal.findingIds) {
      const existing = ids.get(findingId) ?? [];
      existing.push(proposal.id);
      ids.set(findingId, existing);
    }
  }
  return ids;
}

function traceMessageEvidenceLink(index: number): TraceEvidenceLink {
  return {
    kind: "trace_message",
    ref: `msg:${index}`,
    label: `msg #${index}`,
    href: `#msg-${index}`,
  };
}

function artifactEvidenceLink(ref: string): TraceEvidenceLink {
  return {
    kind: "artifact",
    ref,
    label: artifactLabel(ref),
    href: ref,
  };
}

function artifactLabel(ref: string): string {
  const parts = ref.split(/[\\/]/).filter(Boolean);
  return parts.at(-1) ?? ref;
}

function formatEvidenceLabels(refs: readonly TraceEvidenceLink[]): string {
  return refs.length === 0 ? "none" : refs.map((ref) => ref.label).join(", ");
}

function formatList(items: readonly string[]): string {
  return items.length === 0 ? "none" : items.join(", ");
}
