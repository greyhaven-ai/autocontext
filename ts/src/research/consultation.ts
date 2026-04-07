/**
 * Research consultation — goal decomposition and brief assembly (AC-499 TS parity).
 */

import { ResearchEnabledSession } from "./runtime.js";
import { Citation, ResearchQuery, ResearchResult, type Urgency, Urgency as UrgencyEnum } from "./types.js";

function dedupeCitations(results: ResearchResult[]): Citation[] {
  const seen = new Set<string>();
  const unique: Citation[] = [];
  for (const r of results) {
    for (const c of r.citations) {
      const key = `${c.source}||${c.url}`;
      if (!seen.has(key)) { seen.add(key); unique.push(c); }
    }
  }
  return unique;
}

export class ResearchBrief {
  readonly goal: string;
  readonly findings: ResearchResult[];
  readonly uniqueCitations: Citation[];

  constructor(goal: string, findings: ResearchResult[], uniqueCitations: Citation[]) {
    this.goal = goal;
    this.findings = findings;
    this.uniqueCitations = uniqueCitations;
  }

  get avgConfidence(): number {
    if (!this.findings.length) return 0;
    return this.findings.reduce((sum, f) => sum + f.confidence, 0) / this.findings.length;
  }

  static fromResults(goal: string, results: ResearchResult[], minConfidence = 0): ResearchBrief {
    const filtered = results.filter((r) => r.confidence >= minConfidence);
    return new ResearchBrief(goal, filtered, dedupeCitations(filtered));
  }

  static empty(goal: string): ResearchBrief { return new ResearchBrief(goal, [], []); }

  toMarkdown(): string {
    if (!this.findings.length) return `## Research Brief: ${this.goal}\n\nNo findings available.\n`;
    const parts = [`## Research Brief: ${this.goal}\n`];
    for (const f of this.findings) {
      parts.push(`### ${f.queryTopic} (confidence: ${Math.round(f.confidence * 100)}%)\n`);
      parts.push(`${f.summary}\n`);
      for (const c of f.citations) {
        parts.push(c.url ? `- [${c.source}](${c.url})` : `- ${c.source}`);
        if (c.snippet) parts.push(`  > ${c.snippet}`);
      }
      parts.push("");
    }
    return parts.join("\n");
  }

  toJSON(): Record<string, unknown> {
    return { goal: this.goal, findings: this.findings.map((f) => f.toJSON()), uniqueCitations: this.uniqueCitations.map((c) => c.toJSON()) };
  }

  static fromJSON(data: Record<string, unknown>): ResearchBrief {
    const findings = ((data.findings as Record<string, unknown>[]) ?? []).map(ResearchResult.fromJSON);
    const cites = ((data.uniqueCitations as Record<string, unknown>[]) ?? []).map(Citation.fromJSON);
    return new ResearchBrief(data.goal as string, findings, cites);
  }
}

export class ResearchConsultant {
  private _urgency: Urgency;
  private _minConfidence: number;

  constructor(opts?: { urgency?: Urgency; minConfidence?: number }) {
    this._urgency = opts?.urgency ?? UrgencyEnum.NORMAL;
    this._minConfidence = opts?.minConfidence ?? 0;
  }

  consult(session: ResearchEnabledSession, topics: string[], context = ""): ResearchBrief {
    if (!session.hasResearch) return ResearchBrief.empty(session.goal);

    const results: ResearchResult[] = [];
    for (const topic of topics) {
      const result = session.research(new ResearchQuery({ topic, context, urgency: this._urgency }));
      if (!result) break;
      results.push(result);
    }
    return ResearchBrief.fromResults(session.goal, results, this._minConfidence);
  }
}
