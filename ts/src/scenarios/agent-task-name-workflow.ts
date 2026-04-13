const ABSTRACT_SUFFIXES = [
  "ness", "tion", "sion", "ment", "ity", "ous", "ive", "able",
  "ible", "ful", "less", "ence", "ance", "ical", "ally",
] as const;

/** Stop words excluded from derived names.
 * NOTE: Keep in sync with autocontext/src/autocontext/scenarios/custom/agent_task_creator.py STOP_WORDS */
export const AGENT_TASK_NAME_STOP_WORDS = new Set([
  "a", "an", "the", "task", "where", "you", "with", "and", "or", "of", "for",
  "i", "want", "need", "make", "create", "build", "write", "develop", "implement",
  "that", "can", "should", "could", "would", "will", "must",
  "agent", "tool", "system",
  "clear", "well", "good", "great", "very", "really", "also", "just", "structured",
  "it", "we", "they", "is", "are", "was", "be", "do", "does",
  "to", "in", "on", "at", "by", "which", "what", "how",
  "about", "from", "into", "after", "before", "below", "above", "under", "over",
  "using", "via",
  "design", "generate", "generates", "generated", "edit", "analyze", "analyse",
  "find", "add", "remove", "update", "improve",
  "file", "section", "scenario",
  "simple", "complex", "advanced", "word", "multi", "partial", "hidden",
]);

export function scoreAgentTaskNameWord(
  word: string,
  position: number,
  totalWords: number,
): number {
  let score = 0;

  if (ABSTRACT_SUFFIXES.some((suffix) => word.endsWith(suffix))) {
    score -= 2;
  }

  if (word.length >= 4 && word.length <= 12) {
    score += 2;
  } else if (word.length > 2) {
    score += 1;
  }

  if (totalWords > 0) {
    score += 1 - (position / totalWords) * 0.5;
  }

  return score;
}

export function deriveAgentTaskName(description: string): string {
  const words = description
    .toLowerCase()
    .replace(/[^a-z0-9\s]/g, " ")
    .split(/\s+/)
    .filter((word) => word && !AGENT_TASK_NAME_STOP_WORDS.has(word) && word.length > 1);

  const sorted = words
    .map((word, index) => ({
      word,
      index,
      score: scoreAgentTaskNameWord(word, index, words.length),
    }))
    .sort((a, b) => (b.score - a.score) || (a.index - b.index));

  const seen = new Set<string>();
  const unique: string[] = [];
  for (const { word } of sorted) {
    if (!seen.has(word)) {
      seen.add(word);
      unique.push(word);
    }
  }

  const nameWords = unique.length >= 3
    ? unique.slice(0, 3)
    : unique.length > 0
      ? unique.slice(0, 2)
      : ["custom"];
  return nameWords.join("_");
}
