/**
 * Dead-end tracking — track strategies that consistently fail (AC-349 Task 38).
 * Mirrors Python's autocontext/knowledge/dead_end_manager.py.
 */

export class DeadEndEntry {
  generation: number;
  strategySummary: string;
  score: number;
  reason: string;

  constructor(generation: number, strategySummary: string, score: number, reason: string) {
    this.generation = generation;
    this.strategySummary = strategySummary;
    this.score = score;
    this.reason = reason;
  }

  toMarkdown(): string {
    return (
      `- **Gen ${this.generation}**: ${this.strategySummary} ` +
      `(score=${this.score.toFixed(4)}) — ${this.reason}`
    );
  }

  static fromRollback(generation: number, strategy: string, score: number): DeadEndEntry {
    const summary = strategy.length > 80 ? strategy.slice(0, 80) + "..." : strategy;
    return new DeadEndEntry(
      generation,
      summary,
      score,
      "Rolled back due to score regression",
    );
  }
}

export function consolidateDeadEnds(entriesMd: string, maxEntries: number): string {
  const lines = entriesMd.trim().split("\n");
  const entryLines = lines.filter((l) => l.startsWith("- **Gen"));
  if (entryLines.length <= maxEntries) return entriesMd;
  const kept = entryLines.slice(-maxEntries);
  return "# Dead-End Registry\n\n" + kept.join("\n") + "\n";
}
