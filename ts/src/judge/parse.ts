/**
 * Multi-strategy judge response parser.
 *
 * Strategies (tried in order):
 * 1. Marker-based: <!-- JUDGE_RESULT_START/END --> (preferred — matches system prompt format)
 * 2. Shared model-JSON extraction, accepted only if it carries a "score"
 * 3. Plain text: "Score: 0.85" patterns
 *
 * AC-937 collapsed what used to be two separate middle tiers, matching the
 * Python fix in AC-924. A hand-rolled score-shaped-object regex ran ahead of a
 * hand-rolled fence regex, and because it scanned the whole response and took
 * the FIRST match, it read inside fences and reasoning blocks and won before
 * the fence-aware tier was reached. Measured on the Python twin: a reasoning
 * block holding a discarded draft scored the run 0.05 where the fenced answer
 * said 0.88. That is ordinary open-weight output shape, and the failure is
 * silent -- a wrong number entering the ranking, not an error.
 *
 * The "score" requirement stays here, at the call site. `extractJson` is a
 * general model-JSON parser shared with other call sites and has no business
 * knowing about scores.
 */
import { extractJson } from "../execution/model-json.js";

const RESULT_START = "<!-- JUDGE_RESULT_START -->";
const RESULT_END = "<!-- JUDGE_RESULT_END -->";

export type ParseMethod = "raw_json" | "code_block" | "markers" | "plaintext" | "none";

export interface ParsedJudge {
  score: number;
  reasoning: string;
  dimensionScores: Record<string, number>;
  parseMethod: ParseMethod;
}

function clamp(v: number): number {
  return Math.max(0, Math.min(1, v));
}

function extractFromDict(
  data: Record<string, unknown>,
  source: ParseMethod,
): ParsedJudge {
  const raw = Number(data.score ?? 0);
  const score = clamp(isNaN(raw) ? 0 : raw);
  const reasoning = String(data.reasoning ?? "");

  const dims: Record<string, number> = {};
  const dimensions = data.dimensions;
  if (dimensions && typeof dimensions === "object") {
    for (const [k, v] of Object.entries(dimensions as Record<string, unknown>)) {
      const n = Number(v);
      if (!isNaN(n)) dims[k] = clamp(n);
    }
  }

  return { score, reasoning, dimensionScores: dims, parseMethod: source };
}

function tryMarkerParse(response: string): Record<string, unknown> | null {
  const startIdx = response.indexOf(RESULT_START);
  if (startIdx === -1) return null;
  const endIdx = response.indexOf(RESULT_END, startIdx);
  if (endIdx === -1) return null;

  const jsonStr = response
    .slice(startIdx + RESULT_START.length, endIdx)
    .trim();
  try {
    const data = JSON.parse(jsonStr);
    return typeof data === "object" && data !== null ? data : null;
  } catch {
    return null;
  }
}

function tryModelJsonParse(response: string): Record<string, unknown> | null {
  const data = extractJson(response, { onFailure: "none" });
  return data && "score" in data ? data : null;
}

function tryPlaintextParse(response: string): ParsedJudge | null {
  const patterns = [
    /(?:overall\s+)?score[:\s]+([01](?:\.\d+)?)/i,
    /"score"\s*:\s*([01](?:\.\d+)?)/,
    /(\d\.\d+)\s*\/\s*1\.0/,
  ];
  for (const pat of patterns) {
    const m = response.match(pat);
    if (m) {
      const score = parseFloat(m[1]);
      if (score >= 0 && score <= 1) {
        const reasoning = response.length > 500 ? response.slice(0, 500) : response;
        return {
          score,
          reasoning,
          dimensionScores: {},
          parseMethod: "plaintext" as ParseMethod,
        };
      }
    }
  }
  return null;
}

export function parseJudgeResponse(response: string): ParsedJudge {
  // Strategy 1: Markers (preferred — matches our system prompt format)
  const markerData = tryMarkerParse(response);
  if (markerData) return extractFromDict(markerData, "markers");

  // Strategy 2: Shared extraction, gated on judge semantics.
  const jsonData = tryModelJsonParse(response);
  if (jsonData) return extractFromDict(jsonData, "raw_json");

  // Strategy 3: Plaintext
  const plainResult = tryPlaintextParse(response);
  if (plainResult) return plainResult;

  return {
    score: 0,
    reasoning: "Failed to parse judge response: no parseable score found",
    dimensionScores: {},
    parseMethod: "none",
  };
}
