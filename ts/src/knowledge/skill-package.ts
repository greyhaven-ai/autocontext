/**
 * SkillPackage — portable knowledge packages for external agents.
 * Port of autocontext/src/autocontext/knowledge/export.py
 */

export interface SkillPackageData {
  scenarioName: string;
  displayName: string;
  description: string;
  playbook: string;
  lessons: string[];
  bestStrategy: Record<string, unknown> | null;
  bestScore: number;
  bestElo: number;
  hints: string;
  harness?: Record<string, string>;
  metadata?: Record<string, unknown>;
  // Agent task fields
  taskPrompt?: string | null;
  judgeRubric?: string | null;
  exampleOutputs?: Array<{ output: string; score: number; reasoning: string }> | null;
  outputFormat?: string | null;
  referenceContext?: string | null;
  contextPreparation?: string | null;
  maxRounds?: number | null;
  qualityThreshold?: number | null;
}

// Noise patterns for cleaning lesson bullets
const ROLLBACK_RE = /^-\s*Generation\s+\d+\s+ROLLBACK\b/i;
const RAW_JSON_RE = /\{"[a-z_]+"\s*:\s*[\d.]+/;
const SCORE_PARENS_RE = /\(score=[0-9.]+,\s*delta=[0-9.+-]+,\s*threshold=[0-9.]+\)/g;

export class SkillPackage {
  readonly scenarioName: string;
  readonly displayName: string;
  readonly description: string;
  readonly playbook: string;
  readonly lessons: string[];
  readonly bestStrategy: Record<string, unknown> | null;
  readonly bestScore: number;
  readonly bestElo: number;
  readonly hints: string;
  readonly harness: Record<string, string>;
  readonly metadata: Record<string, unknown>;
  readonly taskPrompt: string | null;
  readonly judgeRubric: string | null;
  readonly exampleOutputs: Array<{ output: string; score: number; reasoning: string }> | null;
  readonly outputFormat: string | null;
  readonly referenceContext: string | null;
  readonly contextPreparation: string | null;
  readonly maxRounds: number | null;
  readonly qualityThreshold: number | null;

  constructor(data: SkillPackageData) {
    this.scenarioName = data.scenarioName;
    this.displayName = data.displayName;
    this.description = data.description;
    this.playbook = data.playbook;
    this.lessons = data.lessons;
    this.bestStrategy = data.bestStrategy;
    this.bestScore = data.bestScore;
    this.bestElo = data.bestElo;
    this.hints = data.hints;
    this.harness = data.harness ?? {};
    this.metadata = data.metadata ?? {};
    this.taskPrompt = data.taskPrompt ?? null;
    this.judgeRubric = data.judgeRubric ?? null;
    this.exampleOutputs = data.exampleOutputs ?? null;
    this.outputFormat = data.outputFormat ?? null;
    this.referenceContext = data.referenceContext ?? null;
    this.contextPreparation = data.contextPreparation ?? null;
    this.maxRounds = data.maxRounds ?? null;
    this.qualityThreshold = data.qualityThreshold ?? null;
  }

  toDict(): Record<string, unknown> {
    const d: Record<string, unknown> = {
      scenario_name: this.scenarioName,
      display_name: this.displayName,
      description: this.description,
      playbook: this.playbook,
      lessons: this.lessons,
      best_strategy: this.bestStrategy,
      best_score: this.bestScore,
      best_elo: this.bestElo,
      hints: this.hints,
      harness: this.harness,
      metadata: this.metadata,
    };
    if (this.taskPrompt != null) d.task_prompt = this.taskPrompt;
    if (this.judgeRubric != null) d.judge_rubric = this.judgeRubric;
    if (this.exampleOutputs != null) d.example_outputs = this.exampleOutputs;
    if (this.outputFormat != null) d.output_format = this.outputFormat;
    if (this.referenceContext != null) d.reference_context = this.referenceContext;
    if (this.contextPreparation != null) d.context_preparation = this.contextPreparation;
    if (this.maxRounds != null && this.maxRounds > 1) d.max_rounds = this.maxRounds;
    if (this.qualityThreshold != null) d.quality_threshold = this.qualityThreshold;
    return d;
  }

  toSkillMarkdown(): string {
    const lessonsBlock = this.lessons.length > 0
      ? this.lessons.map((l) => `- ${l}`).join("\n")
      : "No lessons yet.";

    if (this.taskPrompt != null) {
      return this._renderAgentTaskMarkdown(lessonsBlock);
    }

    let strategyBlock = "";
    if (this.bestStrategy) {
      strategyBlock =
        `\n## Best Known Strategy\n\n` +
        `\`\`\`json\n${JSON.stringify(this.bestStrategy, null, 2)}\n\`\`\`\n` +
        `\nBest score: ${this.bestScore.toFixed(4)} | Best Elo: ${this.bestElo.toFixed(1)}\n`;
    }

    let harnessBlock = "";
    const harnessEntries = Object.entries(this.harness).sort(([a], [b]) => a.localeCompare(b));
    if (harnessEntries.length > 0) {
      const parts = ["\n## Harness Validators\n"];
      for (const [name, source] of harnessEntries) {
        parts.push(`\n### ${name}\n\n\`\`\`python\n${source}\n\`\`\`\n`);
      }
      harnessBlock = parts.join("");
    }

    return (
      `---\nname: ${this.scenarioName.replace(/_/g, "-")}-knowledge\n` +
      `description: ${this.description.slice(0, 200)}\n---\n\n` +
      `# ${this.displayName}\n\n` +
      `${this.description}\n\n` +
      `## Operational Lessons\n\n` +
      `${lessonsBlock}\n` +
      `${strategyBlock}\n` +
      `## Playbook\n\n` +
      `${this.playbook}\n` +
      harnessBlock
    );
  }

  private _renderAgentTaskMarkdown(lessonsBlock: string): string {
    const parts: string[] = [
      `---\nname: ${this.scenarioName.replace(/_/g, "-")}-knowledge\n` +
        `description: ${this.description.slice(0, 200)}\n---\n\n` +
        `# ${this.displayName}\n\n` +
        `${this.description}\n\n` +
        `## Task\n\n` +
        `${this.taskPrompt}\n`,
    ];

    if (this.judgeRubric) {
      parts.push(`\n## Evaluation Criteria\n\n${this.judgeRubric}\n`);
    }

    if (this.contextPreparation) {
      parts.push(`\n## Context Preparation\n\n${this.contextPreparation}\n`);
    }

    if (this.referenceContext) {
      parts.push(`\n## Reference Context\n\n${this.referenceContext}\n`);
    }

    if (this.exampleOutputs && this.exampleOutputs.length > 0) {
      parts.push("\n## Example Outputs\n");
      for (const [i, ex] of this.exampleOutputs.slice(0, 3).entries()) {
        parts.push(
          `\n<details>\n<summary>Example ${i + 1} (score: ${ex.score.toFixed(2)})</summary>\n\n` +
            `**Output:**\n\n${ex.output}\n\n` +
            `**Reasoning:** ${ex.reasoning}\n\n` +
            `</details>\n`,
        );
      }
    }

    parts.push(`\n## Operational Lessons\n\n${lessonsBlock}\n`);

    if (this.bestStrategy) {
      parts.push(
        `\n## Best Known Strategy\n\n` +
          `\`\`\`\n${JSON.stringify(this.bestStrategy, null, 2)}\n\`\`\`\n` +
          `\nBest score: ${this.bestScore.toFixed(4)} | Best Elo: ${this.bestElo.toFixed(1)}\n`,
      );
    }

    parts.push(`\n## Playbook\n\n${this.playbook}\n`);

    return parts.join("");
  }
}

/**
 * Convenience builder for agent-task skill packages.
 */
export function exportAgentTaskSkill(opts: {
  scenarioName: string;
  taskPrompt: string;
  judgeRubric: string;
  outputFormat: string;
  playbook: string;
  lessons: string[];
  bestOutputs: Array<{ output: string; score: number; reasoning: string }>;
  hints?: string;
  referenceContext?: string;
  contextPreparation?: string;
}): SkillPackage {
  const displayName = opts.scenarioName.replace(/_/g, " ").replace(/\b\w/g, (c) => c.toUpperCase());
  return new SkillPackage({
    scenarioName: opts.scenarioName,
    displayName,
    description: `Agent task: ${displayName}`,
    playbook: opts.playbook,
    lessons: opts.lessons,
    bestStrategy: null,
    bestScore: opts.bestOutputs.length > 0 ? opts.bestOutputs[0].score : 0.0,
    bestElo: 1500.0,
    hints: opts.hints ?? "",
    taskPrompt: opts.taskPrompt,
    judgeRubric: opts.judgeRubric,
    exampleOutputs: opts.bestOutputs.length > 0 ? opts.bestOutputs : null,
    outputFormat: opts.outputFormat,
    referenceContext: opts.referenceContext ?? null,
    contextPreparation: opts.contextPreparation ?? null,
  });
}

/**
 * Clean lesson bullets: strip AutoContext-internal noise, keeping prescriptive rules.
 */
export function cleanLessons(rawBullets: string[]): string[] {
  const cleaned: string[] = [];
  for (const bullet of rawBullets) {
    const text = bullet.trim();
    if (!text) continue;
    let content = text.startsWith("- ") ? text.slice(2) : text;
    if (ROLLBACK_RE.test(text)) continue;
    if (RAW_JSON_RE.test(content) && content.trim().startsWith("{")) continue;
    content = content.replace(SCORE_PARENS_RE, "").trim();
    if (content) cleaned.push(content);
  }
  return cleaned;
}
