export interface CompetitorPromptParts {
  scenarioName: string;
  scenarioRules: string;
  strategyInterface: string;
  evaluationCriteria: string;
  playbook: string;
  trajectory?: string;
  deadEnds?: string;
  sessionReports?: string;
  freshStartHint?: string | null;
  operatorHint?: string | null;
}

export interface SupportPromptParts {
  role: "analyst" | "coach";
  scenarioName: string;
  scenarioRules: string;
  strategyInterface: string;
  strategyJson: Record<string, unknown>;
  analysisSummary: string;
  playbook: string;
  trajectory?: string;
  deadEnds?: string;
}

export interface CuratorPromptParts {
  tournamentSummary: string;
  currentPlaybook: string;
  proposedPlaybook: string;
  trajectory?: string;
}

export interface CuratorConsolidationPromptParts {
  lessons: string;
  skillMaxLessons: number;
}

export function buildCompetitorPrompt(parts: CompetitorPromptParts): string {
  const sections = [
    `Describe your strategy for the ${parts.scenarioName} scenario. Return JSON with the strategy parameters.`,
    `Scenario Rules:\n${parts.scenarioRules}`,
    `Strategy Interface:\n${parts.strategyInterface}`,
    `Evaluation Criteria:\n${parts.evaluationCriteria}`,
    `Current Playbook:\n${parts.playbook}`,
  ];

  if (parts.trajectory) {
    sections.push(`Recent Score Trajectory:\n${parts.trajectory}`);
  }
  if (parts.deadEnds) {
    sections.push(`Known Dead Ends (do not repeat these approaches):\n${parts.deadEnds}`);
  }
  if (parts.sessionReports) {
    sections.push(`Prior Session Reports:\n${parts.sessionReports}`);
  }
  if (parts.freshStartHint) {
    sections.push(`Fresh Start Guidance:\n${parts.freshStartHint}`);
  }
  if (parts.operatorHint) {
    sections.push(`Operator Hint:\n${parts.operatorHint}`);
  }

  sections.push("Respond with JSON only. Include the strategy fields required by the strategy interface.");
  return sections.join("\n\n");
}

export function buildSupportPrompt(parts: SupportPromptParts): string {
  const intro =
    parts.role === "analyst"
      ? `Analyze strengths/failures of the current strategy for ${parts.scenarioName}.`
      : `You are the playbook coach. Update the playbook for ${parts.scenarioName}.`;

  const sections = [
    intro,
    `Scenario Rules:\n${parts.scenarioRules}`,
    `Strategy Interface:\n${parts.strategyInterface}`,
    `Current Strategy JSON:\n${JSON.stringify(parts.strategyJson, null, 2)}`,
    `Tournament Summary:\n${parts.analysisSummary}`,
    `Current Playbook:\n${parts.playbook}`,
  ];

  if (parts.trajectory) {
    sections.push(`Recent Score Trajectory:\n${parts.trajectory}`);
  }
  if (parts.deadEnds) {
    sections.push(`Known Dead Ends:\n${parts.deadEnds}`);
  }

  return sections.join("\n\n");
}

export function buildCuratorPrompt(parts: CuratorPromptParts): string {
  const sections = [
    "You are a curator assessing playbook quality. Compare the CURRENT and PROPOSED playbooks.",
    "Respond with reasoning, then include the following markers:",
    "<!-- CURATOR_DECISION: accept|reject|merge -->",
    "<!-- CURATOR_SCORE: 0-10 -->",
    "If merging, include:",
    "<!-- CURATOR_PLAYBOOK_START -->",
    "...merged playbook...",
    "<!-- CURATOR_PLAYBOOK_END -->",
    `Tournament Summary:\n${parts.tournamentSummary}`,
    `Current Playbook:\n${parts.currentPlaybook}`,
    `Proposed Playbook:\n${parts.proposedPlaybook}`,
  ];

  if (parts.trajectory) {
    sections.push(`Recent Score Trajectory:\n${parts.trajectory}`);
  }

  return sections.join("\n\n");
}

export function buildCuratorConsolidationPrompt(parts: CuratorConsolidationPromptParts): string {
  return [
    "You are a curator consolidating operational lessons.",
    `Reduce duplication and keep at most ${parts.skillMaxLessons} lessons.`,
    "Respond with reasoning, then include the following markers:",
    "<!-- CONSOLIDATED_LESSONS_START -->",
    "...bullet lessons...",
    "<!-- CONSOLIDATED_LESSONS_END -->",
    "<!-- LESSONS_REMOVED: N -->",
    `Existing Lessons:\n${parts.lessons}`,
  ].join("\n\n");
}
