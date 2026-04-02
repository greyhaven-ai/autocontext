/**
 * Research prompt wiring — format briefs for LLM injection (AC-501 TS parity).
 */

import { ResearchBrief } from "./consultation.js";

const PLACEHOLDER = "{research}";
const DEFAULT_MAX_CHARS = 4000;

export class ResearchPromptInjector {
  private maxChars: number;

  constructor(opts?: { maxChars?: number }) {
    this.maxChars = opts?.maxChars ?? DEFAULT_MAX_CHARS;
  }

  formatBrief(brief: ResearchBrief): string {
    if (!brief.findings.length) return "";

    const sorted = [...brief.findings].sort((a, b) => b.confidence - a.confidence);
    const header = `## External Research: ${brief.goal}\n`;
    const parts = [header];
    let budget = this.maxChars - header.length;

    for (const f of sorted) {
      const lines = [`**${f.queryTopic}** (confidence: ${Math.round(f.confidence * 100)}%)`];
      lines.push(f.summary);
      for (const c of f.citations) {
        lines.push(c.url ? `- [${c.source}](${c.url})` : `- ${c.source}`);
      }
      lines.push("");
      const block = lines.join("\n");

      if (block.length > budget) {
        if (parts.length === 1) parts.push(block.slice(0, budget));
        break;
      }
      parts.push(block);
      budget -= block.length;
    }
    return parts.join("\n");
  }

  inject(basePrompt: string, brief: ResearchBrief): string {
    const section = this.formatBrief(brief);
    if (!section) return basePrompt;
    if (basePrompt.includes(PLACEHOLDER)) return basePrompt.replace(PLACEHOLDER, section);
    return `${basePrompt}\n\n${section}`;
  }
}
