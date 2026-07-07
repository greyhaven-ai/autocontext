// Deterministic, pure repair functions for harness surfaces (AC-878).
//
// This is the TypeScript half of the AC-878 parity pair. The reference
// semantics live in
// `autocontext/src/autocontext/harness_optimization/repairs.py`; this module
// reproduces the SAME repair decisions (status / reason / target) on the SAME
// inputs. A shared repo-root fixture
// (`fixtures/harness-optimization/repair-cases/repair-cases.json`) is loaded by
// both languages' tests to prove the two implementations agree.
//
// Each repair is a PURE function over recorded state: it never calls a model,
// never reads answer hints, never touches the filesystem, and is fully
// replayable. A repair inspects the state it is handed, decides whether a known
// failure mode is present, and returns a `RepairResult` describing what it did
// (or why it declined). The gate that owns side effects (writing files,
// breaking a loop, rejecting a finish) acts on the decision; these functions
// only decide.

import type { RepairResult } from "./contract/generated-types.js";
import {
  probeArtifactContract,
  type ArtifactContractProbeInputs,
} from "../control-plane/contract-probes/index.js";

/**
 * Fresh cross-language parity stamp for a TypeScript-implemented repair.
 *
 * Python parity is pending until its mirror lands (here it already has); the
 * schema hash is empty because these repairs share the RepairResult schema,
 * whose hash is stamped by the sync tooling, not per-call. This mirrors the
 * Python `_parity()` helper with the implemented/pending sides flipped.
 */
function parity(): RepairResult["parity"] {
  return { python: "pending", typescript: "implemented", schema_hash: "" };
}

// ---------------------------------------------------------------------------
// repair 1: tool-call JSON (structural-only)
// ---------------------------------------------------------------------------

/**
 * Return the inside of a ```...``` / ```json...``` fence, else null.
 *
 * Structural wrapper removal only: the returned text is a verbatim slice of the
 * fenced body, so no field value is altered.
 */
function stripCodeFence(raw: string): string | null {
  const stripped = raw.trim();
  if (!stripped.startsWith("```")) return null;
  const lines = stripped.split(/\r\n|\r|\n/);
  if (lines.length < 2) return null;
  if (!lines[0].startsWith("```")) return null;
  if (lines[lines.length - 1].trim() !== "```") return null;
  return lines.slice(1, -1).join("\n");
}

/**
 * Drop structural commas that sit immediately before a `}` or `]`.
 *
 * String-aware: a comma inside a JSON string literal is copied verbatim, so a
 * value like `"a,]"` is never mutated. Only a comma the grammar forbids (right
 * before a closer) is removed.
 */
function removeTrailingCommas(raw: string): string {
  const out: string[] = [];
  let i = 0;
  const n = raw.length;
  let inString = false;
  let escape = false;
  while (i < n) {
    const ch = raw[i];
    if (inString) {
      out.push(ch);
      if (escape) {
        escape = false;
      } else if (ch === "\\") {
        escape = true;
      } else if (ch === '"') {
        inString = false;
      }
      i += 1;
      continue;
    }
    if (ch === '"') {
      inString = true;
      out.push(ch);
      i += 1;
      continue;
    }
    if (ch === ",") {
      let j = i + 1;
      while (j < n && " \t\r\n".includes(raw[j])) {
        j += 1;
      }
      if (j < n && (raw[j] === "}" || raw[j] === "]")) {
        // trailing comma before a closer: drop it, keep the closer.
        i += 1;
        continue;
      }
    }
    out.push(ch);
    i += 1;
  }
  return out.join("");
}

/**
 * Append one closer iff exactly one brace/bracket is left open.
 *
 * Returns null when the truncation is ambiguous: zero unclosed openers, more
 * than one unclosed opener (multiple plausible closings), or a truncation that
 * lands inside a string literal.
 */
function closeSingleUnclosed(raw: string): string | null {
  const stack: string[] = [];
  let inString = false;
  let escape = false;
  for (const ch of raw) {
    if (inString) {
      if (escape) {
        escape = false;
      } else if (ch === "\\") {
        escape = true;
      } else if (ch === '"') {
        inString = false;
      }
      continue;
    }
    if (ch === '"') {
      inString = true;
    } else if (ch === "{" || ch === "[") {
      stack.push(ch);
    } else if (ch === "}" || ch === "]") {
      if (stack.length > 0) stack.pop();
    }
  }
  if (inString) return null;
  if (stack.length !== 1) return null;
  return raw + (stack[0] === "{" ? "}" : "]");
}

function joinReason(prefix: string, suffix: string): string {
  return prefix ? `${prefix}; ${suffix}` : suffix;
}

/**
 * Ordered (reason, candidate) structural repairs to try, most-local first.
 *
 * Every candidate is derived from `raw` by structural transforms only (fence
 * strip, trailing-comma drop, single-closer append), so none can introduce or
 * change a field value.
 */
function structuralAttempts(raw: string): Array<[string, string]> {
  const attempts: Array<[string, string]> = [];

  const bases: Array<[string, string]> = [["", raw]];
  const fenced = stripCodeFence(raw);
  if (fenced !== null && fenced !== raw) {
    bases.push(["stripped markdown code fence", fenced]);
  }

  for (const [baseReason, base] of bases) {
    if (baseReason) {
      attempts.push([baseReason, base]);
    }
    const noComma = removeTrailingCommas(base);
    if (noComma !== base) {
      attempts.push([joinReason(baseReason, "removed trailing comma before closer"), noComma]);
    }
    const closed = closeSingleUnclosed(base);
    if (closed !== null && closed !== base) {
      attempts.push([joinReason(baseReason, "closed a single truncated brace/bracket"), closed]);
    }
    if (noComma !== base) {
      const both = closeSingleUnclosed(noComma);
      if (both !== null && both !== noComma) {
        attempts.push([
          joinReason(baseReason, "removed trailing comma and closed a truncated brace/bracket"),
          both,
        ]);
      }
    }
  }
  return attempts;
}

function isValidJson(raw: string): boolean {
  try {
    JSON.parse(raw);
    return true;
  } catch {
    return false;
  }
}

/**
 * Structurally repair malformed tool-call JSON without touching values.
 *
 * Returns `{ value: json, result }` when already valid or repaired, and
 * `{ value: null, result }` when the input is ambiguous or unrecoverable by
 * structural means. Field values are never guessed or altered.
 */
export function repairToolCallJson(raw: string): { value: string | null; result: RepairResult } {
  if (isValidJson(raw)) {
    return {
      value: raw,
      result: {
        schema_version: 1,
        repair_name: "tool_call_json",
        status: "not_applicable",
        reason: "already valid json",
        target: "",
        before: { valid: true },
        after: { valid: true },
        parity: parity(),
      },
    };
  }

  for (const [reason, candidate] of structuralAttempts(raw)) {
    if (!isValidJson(candidate)) continue;
    return {
      value: candidate,
      result: {
        schema_version: 1,
        repair_name: "tool_call_json",
        status: "applied",
        reason: `structural repair: ${reason}`,
        target: "",
        before: { valid: false },
        after: { valid: true },
        parity: parity(),
      },
    };
  }

  return {
    value: null,
    result: {
      schema_version: 1,
      repair_name: "tool_call_json",
      status: "skipped",
      reason: "ambiguous or unrecoverable tool json",
      target: "",
      before: { valid: false },
      after: { valid: false },
      parity: parity(),
    },
  };
}

// ---------------------------------------------------------------------------
// repair 2: artifact landing (relocate by matching existing content)
// ---------------------------------------------------------------------------

/** Rebuild the expected contract against a candidate path+content, in memory. */
function contractFor(
  path: string,
  content: string,
  expected: ArtifactContractProbeInputs,
): ArtifactContractProbeInputs {
  return {
    path,
    content,
    expectedLineEnding: expected.expectedLineEnding,
    requiredSubstrings: expected.requiredSubstrings,
    forbiddenSubstrings: expected.forbiddenSubstrings,
    requiredJsonFields: expected.requiredJsonFields,
  };
}

/**
 * Detect the "right content, wrong path" landing mistake, purely.
 *
 * `produced` maps produced_path -> file content (cached in memory). If the
 * expected contract already passes, this is `not_applicable`. Otherwise it
 * searches `produced` for a path whose content satisfies the contract; when
 * found at a different path, it returns that path as the relocation target. It
 * performs no filesystem writes: the gate relocates, this function decides.
 */
export function repairArtifactLanding({
  expected,
  produced,
}: {
  expected: ArtifactContractProbeInputs;
  produced: Record<string, string>;
}): { path: string | null; result: RepairResult } {
  if (probeArtifactContract(expected).passed) {
    return {
      path: null,
      result: {
        schema_version: 1,
        repair_name: "artifact_landing",
        status: "not_applicable",
        reason: "expected artifact already satisfies the contract",
        target: "",
        before: { landed: true },
        after: { landed: true },
        parity: parity(),
      },
    };
  }

  for (const [producedPath, content] of Object.entries(produced)) {
    if (producedPath === expected.path) continue;
    if (probeArtifactContract(contractFor(producedPath, content, expected)).passed) {
      return {
        path: producedPath,
        result: {
          schema_version: 1,
          repair_name: "artifact_landing",
          status: "applied",
          reason: "expected content found at a different path; relocate",
          target: producedPath,
          before: { landed: false },
          after: { landed: true, source_path: producedPath },
          parity: parity(),
        },
      };
    }
  }

  return {
    path: null,
    result: {
      schema_version: 1,
      repair_name: "artifact_landing",
      status: "skipped",
      reason: "no produced artifact matches the expected contract",
      target: "",
      before: { landed: false },
      after: { landed: false },
      parity: parity(),
    },
  };
}

// ---------------------------------------------------------------------------
// repair 3: finish guard (validate completion before accepting done)
// ---------------------------------------------------------------------------

/**
 * Reject a done claim when completion conditions are not met.
 *
 * This validates completion; it never fabricates completion. When the run
 * claims done but `completionOk` is false, the finish is rejected.
 */
export function finishGuard({
  claimedDone,
  completionOk,
  reasonIfNot,
}: {
  claimedDone: boolean;
  completionOk: boolean;
  reasonIfNot: string;
}): RepairResult {
  if (claimedDone && !completionOk) {
    return {
      schema_version: 1,
      repair_name: "finish_guard",
      status: "applied",
      reason: `finish rejected: ${reasonIfNot}`,
      target: "",
      before: { claimed_done: true },
      after: { accepted_done: false },
      parity: parity(),
    };
  }

  return {
    schema_version: 1,
    repair_name: "finish_guard",
    status: "not_applicable",
    reason: "no unmet completion claim to reject",
    target: "",
    before: { claimed_done: claimedDone },
    after: { accepted_done: claimedDone },
    parity: parity(),
  };
}
