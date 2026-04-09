import { describe, expect, it } from "vitest";

import {
  buildCompetitorPrompt,
  buildCuratorConsolidationPrompt,
  buildCuratorPrompt,
  buildSupportPrompt,
} from "../src/loop/generation-prompts.js";

describe("generation prompt builders", () => {
  it("builds competitor prompts with only populated optional sections", () => {
    const prompt = buildCompetitorPrompt({
      scenarioName: "linear_outage_escalation",
      scenarioRules: "Ask for clarification when evidence is weak.",
      strategyInterface: "Return JSON with escalation_readiness.",
      evaluationCriteria: "Maximize correct escalation decisions.",
      playbook: "Current playbook",
      trajectory: "score up",
      deadEnds: "Do not over-escalate",
      sessionReports: "prior session summary",
      freshStartHint: "Try fewer clarifications",
      operatorHint: "Focus on customer impact",
    });

    expect(prompt).toContain("Describe your strategy for the linear_outage_escalation scenario");
    expect(prompt).toContain("Recent Score Trajectory:\nscore up");
    expect(prompt).toContain("Known Dead Ends (do not repeat these approaches):\nDo not over-escalate");
    expect(prompt).toContain("Prior Session Reports:\nprior session summary");
    expect(prompt).toContain("Fresh Start Guidance:\nTry fewer clarifications");
    expect(prompt).toContain("Operator Hint:\nFocus on customer impact");
  });

  it("omits empty optional competitor sections", () => {
    const prompt = buildCompetitorPrompt({
      scenarioName: "linear_outage_escalation",
      scenarioRules: "Rules",
      strategyInterface: "Interface",
      evaluationCriteria: "Criteria",
      playbook: "Playbook",
      trajectory: "",
      deadEnds: "",
      sessionReports: "",
      freshStartHint: null,
      operatorHint: null,
    });

    expect(prompt).not.toContain("Recent Score Trajectory:");
    expect(prompt).not.toContain("Known Dead Ends");
    expect(prompt).not.toContain("Prior Session Reports:");
    expect(prompt).not.toContain("Fresh Start Guidance:");
    expect(prompt).not.toContain("Operator Hint:");
  });

  it("builds support prompts for analyst and coach roles", () => {
    const analystPrompt = buildSupportPrompt({
      role: "analyst",
      scenarioName: "linear_outage_escalation",
      scenarioRules: "Rules",
      strategyInterface: "Interface",
      strategyJson: { escalation_readiness: 0.8 },
      analysisSummary: "Gate decision: advance",
      playbook: "Playbook",
      trajectory: "trajectory",
      deadEnds: "dead end",
    });
    const coachPrompt = buildSupportPrompt({
      role: "coach",
      scenarioName: "linear_outage_escalation",
      scenarioRules: "Rules",
      strategyInterface: "Interface",
      strategyJson: { escalation_readiness: 0.8 },
      analysisSummary: "Gate decision: advance",
      playbook: "Playbook",
      trajectory: "",
      deadEnds: "",
    });

    expect(analystPrompt).toContain("Analyze strengths/failures");
    expect(analystPrompt).toContain("Known Dead Ends:\ndead end");
    expect(coachPrompt).toContain("You are the playbook coach");
    expect(coachPrompt).not.toContain("Known Dead Ends:");
  });

  it("builds curator prompts with optional trajectory", () => {
    const prompt = buildCuratorPrompt({
      tournamentSummary: "Gate=advance, Best=0.8, Mean=0.7",
      currentPlaybook: "Current",
      proposedPlaybook: "Proposed",
      trajectory: "recent trajectory",
    });

    expect(prompt).toContain("<!-- CURATOR_DECISION: accept|reject|merge -->");
    expect(prompt).toContain("Current Playbook:\nCurrent");
    expect(prompt).toContain("Proposed Playbook:\nProposed");
    expect(prompt).toContain("Recent Score Trajectory:\nrecent trajectory");
  });

  it("builds curator consolidation prompts using lesson limits", () => {
    const prompt = buildCuratorConsolidationPrompt({
      lessons: "- lesson one\n- lesson two",
      skillMaxLessons: 12,
    });

    expect(prompt).toContain("Reduce duplication and keep at most 12 lessons.");
    expect(prompt).toContain("<!-- CONSOLIDATED_LESSONS_START -->");
    expect(prompt).toContain("Existing Lessons:\n- lesson one");
  });
});
