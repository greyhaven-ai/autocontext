/**
 * Intent validator — validate generated scenario matches user's intent (AC-348 Task 31).
 * Uses keyword overlap to estimate how well the generated spec captures the original request.
 */

import { normalizeConfidence } from "../analytics/number-utils.js";

export interface IntentValidationResult {
  valid: boolean;
  confidence: number;
  issues: string[];
}

/**
 * Extract significant words from text (lowercase, deduplicated, stop-word filtered).
 */
function extractKeywords(text: string): Set<string> {
  const stopWords = new Set([
    "a", "an", "the", "is", "are", "was", "were", "be", "been", "being",
    "have", "has", "had", "do", "does", "did", "will", "would", "could",
    "should", "may", "might", "shall", "can", "need", "must", "ought",
    "i", "you", "he", "she", "it", "we", "they", "me", "him", "her",
    "us", "them", "my", "your", "his", "its", "our", "their", "mine",
    "yours", "hers", "ours", "theirs", "this", "that", "these", "those",
    "and", "but", "or", "nor", "for", "yet", "so", "in", "on", "at",
    "to", "of", "by", "from", "with", "about", "between", "through",
    "during", "before", "after", "above", "below", "up", "down", "out",
    "off", "over", "under", "again", "further", "then", "once", "here",
    "there", "when", "where", "why", "how", "all", "both", "each",
    "few", "more", "most", "other", "some", "such", "no", "not", "only",
    "own", "same", "than", "too", "very", "just", "because", "as",
    "until", "while", "if", "into", "test", "want", "scenario", "create",
  ]);

  return new Set(
    text
      .toLowerCase()
      .replace(/[^a-z0-9\s]/g, " ")
      .split(/\s+/)
      .filter((w) => w.length > 2 && !stopWords.has(w)),
  );
}

export class IntentValidator {
  private minConfidence: number;

  constructor(minConfidence = 0.3) {
    this.minConfidence = minConfidence;
  }

  validate(
    intent: string,
    spec: {
      name: string;
      taskPrompt: string;
      rubric: string;
      description: string;
    },
  ): IntentValidationResult {
    // Empty intent means no constraints — always valid
    if (!intent.trim()) {
      return { valid: true, confidence: 1.0, issues: [] };
    }

    const intentKeywords = extractKeywords(intent);
    if (intentKeywords.size === 0) {
      return { valid: true, confidence: 1.0, issues: [] };
    }

    // Combine all spec text for keyword matching
    const specText = [spec.name, spec.taskPrompt, spec.rubric, spec.description].join(" ");
    const specKeywords = extractKeywords(specText);

    // Calculate overlap
    let matchCount = 0;
    for (const keyword of intentKeywords) {
      if (specKeywords.has(keyword)) {
        matchCount++;
      } else {
        // Partial match: check if any spec keyword contains or is contained by intent keyword
        for (const sk of specKeywords) {
          if (sk.includes(keyword) || keyword.includes(sk)) {
            matchCount += 0.5;
            break;
          }
        }
      }
    }

    const confidence = normalizeConfidence(matchCount / intentKeywords.size);
    const issues: string[] = [];

    if (confidence < this.minConfidence) {
      const missingKeywords = [...intentKeywords].filter(
        (k) => ![...specKeywords].some((sk) => sk.includes(k) || k.includes(sk)),
      );
      if (missingKeywords.length > 0) {
        issues.push(
          `Generated scenario does not address these intent keywords: ${missingKeywords.join(", ")}`,
        );
      }
      issues.push(
        `Intent-spec confidence ${confidence.toFixed(2)} is below threshold ${this.minConfidence.toFixed(2)}`,
      );
    }

    return {
      valid: confidence >= this.minConfidence,
      confidence,
      issues,
    };
  }
}
