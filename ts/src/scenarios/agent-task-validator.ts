/**
 * AgentTaskValidator — validates AgentTaskSpec for completeness.
 * Port of autocontext/src/autocontext/scenarios/custom/agent_task_validator.py
 *
 * Note: In TS we don't do code generation/execution validation.
 * Instead we use Zod for spec validation and a factory for instantiation.
 */

import { AgentTaskSpecSchema } from "./agent-task-spec.js";
import type { AgentTaskSpec } from "./agent-task-spec.js";

const INTENT_STOP_WORDS = new Set([
  "a", "an", "the", "and", "or", "of", "for", "to", "in", "on", "at", "by",
  "is", "are", "was", "be", "do", "does", "it", "we", "they", "i", "you",
  "that", "can", "should", "could", "would", "will", "must", "with", "which",
  "what", "how", "task", "agent", "system", "create", "build", "write", "make",
  "good", "well", "very", "just", "also", "clear", "structured", "want", "need",
]);

const TASK_FAMILIES: Record<string, Set<string>> = {
  code: new Set([
    "code", "coding", "python", "function", "algorithm", "program", "debug",
    "debugging", "syntax", "compile", "runtime", "api", "endpoint", "scraper",
    "refactor", "test", "tests", "testing", "unittest", "bug", "bugs",
    "implementation", "implement", "software", "developer", "class", "method",
  ]),
  writing: new Set([
    "essay", "article", "blog", "write", "writing", "prose", "paragraph",
    "narrative", "story", "fiction", "poetry", "haiku", "poem", "literary",
    "persuasive", "rhetoric", "composition", "draft", "editorial", "recipe",
    "cookbook", "cooking", "ingredients", "frosting", "cake", "baking",
  ]),
  analysis: new Set([
    "analysis", "analyze", "diagnostic", "diagnose", "investigate", "root",
    "cause", "debugging", "logs", "monitoring", "crash", "error", "incident",
    "forensic", "audit", "trace", "profiling", "performance", "bottleneck",
  ]),
  data: new Set([
    "data", "dataset", "classification", "classifier", "sentiment", "nlp",
    "machine", "learning", "model", "training", "prediction", "regression",
    "clustering", "neural", "deep", "statistics", "statistical", "inference",
  ]),
  design: new Set([
    "architecture", "design", "pattern", "microservices", "distributed",
    "scalability", "infrastructure", "devops", "deployment", "kubernetes",
    "docker", "cloud", "aws", "system", "systems",
  ]),
};

const CODE_INTENT_SIGNALS = [
  "code", "function", "class", "algorithm", "program", "implement",
  "script", "python", "javascript", "typescript", "java", "rust", "go",
  "generate code", "write code", "coding", "scraper", "web scraper",
];

const CODE_EVALUATION_SIGNALS = [
  "evaluate", "review", "assess", "analyze", "analyse", "audit", "quality",
  "correctness", "diagnostic", "diagnose", "critique", "score", "grade",
];

const TEXT_INTENT_SIGNALS = [
  "essay", "article", "blog", "story", "write about", "persuasive",
  "narrative", "poem", "haiku", "report", "documentation", "recipe",
];

const JSON_INTENT_SIGNALS = [
  "json", "json schema", "structured output", "structured response",
  "return a schema", "return schema", "fields", "field names", "key value",
  "key-value", "object with", "array of", "machine readable", "machine-readable",
];

function extractKeywords(text: string): Set<string> {
  const words = text.toLowerCase().replace(/[^a-z0-9\s]/g, " ").split(/\s+/);
  return new Set(words.filter((word) => word && !INTENT_STOP_WORDS.has(word) && word.length > 1));
}

function detectTaskFamily(keywords: Set<string>): string | null {
  let bestFamily: string | null = null;
  let bestOverlap = 0;
  for (const [family, familyWords] of Object.entries(TASK_FAMILIES)) {
    const overlap = [...keywords].filter((word) => familyWords.has(word)).length;
    if (overlap > bestOverlap) {
      bestOverlap = overlap;
      bestFamily = family;
    }
  }
  return bestOverlap >= 1 ? bestFamily : null;
}

function fuzzyOverlap(a: Set<string>, b: Set<string>, minPrefix = 4): Set<string> {
  const matched = new Set<string>();
  for (const wordA of a) {
    if (b.has(wordA)) {
      matched.add(wordA);
      continue;
    }
    if (wordA.length < minPrefix) {
      continue;
    }
    for (const wordB of b) {
      if (wordB.length < minPrefix) {
        continue;
      }
      const shorter = Math.min(wordA.length, wordB.length);
      const prefixLen = Math.max(minPrefix, shorter - 2);
      if (wordA.slice(0, prefixLen) === wordB.slice(0, prefixLen)) {
        matched.add(wordA);
        break;
      }
    }
  }
  return matched;
}

export function validateIntent(userDescription: string, spec: AgentTaskSpec): string[] {
  if (!userDescription.trim()) {
    return [];
  }

  const errors: string[] = [];
  const descLower = userDescription.toLowerCase();
  const descKeywords = extractKeywords(userDescription);
  const specKeywords = extractKeywords(`${spec.taskPrompt} ${spec.judgeRubric}`);

  const descFamily = detectTaskFamily(descKeywords);
  const specFamily = detectTaskFamily(specKeywords);
  if (descFamily && specFamily && descFamily !== specFamily) {
    errors.push(
      `intent mismatch: description suggests '${descFamily}' task family but generated spec resembles '${specFamily}'`,
    );
  }

  if (descKeywords.size > 0 && specKeywords.size > 0) {
    const overlap = fuzzyOverlap(descKeywords, specKeywords);
    const overlapRatio = overlap.size / descKeywords.size;
    if (overlapRatio === 0 && descKeywords.size >= 2) {
      errors.push(
        "intent drift: no domain keywords from the description appear in the generated task prompt or rubric",
      );
    }
  }

  const descSignalsCode = CODE_INTENT_SIGNALS.some((signal) => descLower.includes(signal));
  const descSignalsText = TEXT_INTENT_SIGNALS.some((signal) => descLower.includes(signal));
  const descSignalsCodeEval = CODE_EVALUATION_SIGNALS.some((signal) => descLower.includes(signal));
  const descSignalsJson = JSON_INTENT_SIGNALS.some((signal) => descLower.includes(signal));

  if (descSignalsCode && !descSignalsText && !descSignalsCodeEval && spec.outputFormat === "free_text") {
    errors.push("format mismatch: description implies code output but spec uses outputFormat='free_text'");
  }
  if (descSignalsText && !descSignalsCode && spec.outputFormat === "code") {
    errors.push("format mismatch: description implies text output but spec uses outputFormat='code'");
  }
  if (descSignalsJson && spec.outputFormat !== "json_schema") {
    errors.push(
      `format mismatch: description implies structured JSON output but spec uses outputFormat='${spec.outputFormat}'`,
    );
  }

  return errors;
}

/**
 * Validate an AgentTaskSpec for completeness and correctness.
 * Returns an array of error strings (empty = valid).
 */
export function validateSpec(spec: AgentTaskSpec): string[] {
  const result = AgentTaskSpecSchema.safeParse(spec);
  if (!result.success) {
    return result.error.issues.map(
      (issue) => `${issue.path.join(".")}: ${issue.message}`,
    );
  }
  return [];
}
