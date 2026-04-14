import type { PromptContextLike } from "./prompt-alignment-types.js";

export const KNOWN_PROMPT_SECTIONS = [
  "Scenario Rules",
  "Strategy Interface",
  "Evaluation Criteria",
  "Current Playbook",
  "Operational Lessons",
  "Available Tools",
  "Competitor Hints",
  "Previous Analysis",
  "Your Task",
  "Playbook",
] as const;

export const REQUIRED_SYSTEM_PROMPT_SECTIONS = [
  "Scenario Rules",
  "Evaluation Criteria",
] as const;

export function readPromptContextString(
  context: PromptContextLike,
  ...keys: string[]
): string {
  for (const key of keys) {
    const value = context[key];
    if (typeof value === "string" && value.trim()) {
      return value.trim();
    }
  }
  return "";
}

export function formatPromptTrajectory(value: unknown): string {
  if (typeof value === "string") {
    return value.trim();
  }
  if (!Array.isArray(value)) {
    return "";
  }

  return value
    .map((entry, index) => {
      if (!entry || typeof entry !== "object") {
        return "";
      }
      const row = entry as Record<string, unknown>;
      const generation = typeof row.generation_index === "number"
        ? row.generation_index
        : index + 1;
      const score = typeof row.best_score === "number"
        ? row.best_score.toFixed(4)
        : "unknown";
      const gate = typeof row.gate_decision === "string"
        ? row.gate_decision
        : "unknown";
      return `Generation ${generation}: score=${score}, gate=${gate}`;
    })
    .filter(Boolean)
    .join("\n");
}

export function extractPromptSections(text: string): string[] {
  const sections: string[] = [];
  const textLower = text.toLowerCase();

  for (const section of KNOWN_PROMPT_SECTIONS) {
    const sectionLower = section.toLowerCase();
    const patterns = [
      `## ${sectionLower}`,
      `# ${sectionLower}`,
      `### ${sectionLower}`,
      `**${sectionLower}**`,
    ];
    if (patterns.some((pattern) => textLower.includes(pattern))) {
      sections.push(section);
    }
  }

  return sections;
}

export function measurePromptWordOverlap(left: string, right: string): number {
  const leftWords = new Set(left.toLowerCase().split(/\s+/));
  const rightWords = new Set(right.toLowerCase().split(/\s+/));
  const overlap = [...leftWords].filter((word) => rightWords.has(word)).length;
  return overlap / Math.max(leftWords.size, rightWords.size);
}
