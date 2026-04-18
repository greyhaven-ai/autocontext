import { describe, test, expect } from "vitest";
import fc from "fast-check";
import {
  isAllowedTransition,
  nextStatesFrom,
  ACTIVATION_STATES,
} from "../../../src/control-plane/promotion/transitions.js";
import { createArtifact, createPromotionEvent } from "../../../src/control-plane/contract/factories.js";
import { appendPromotionEvent } from "../../../src/control-plane/promotion/append.js";
import type { ActivationState, Provenance } from "../../../src/control-plane/contract/types.js";

const anyProvenance: Provenance = {
  authorType: "human",
  authorId: "test@example.com",
  parentArtifactIds: [],
  createdAt: "2026-04-17T12:00:00.000Z",
};

describe("isAllowedTransition — valid forward flow", () => {
  test("candidate → shadow", () => { expect(isAllowedTransition("candidate", "shadow")).toBe(true); });
  test("shadow → canary", () => { expect(isAllowedTransition("shadow", "canary")).toBe(true); });
  test("canary → active", () => { expect(isAllowedTransition("canary", "active")).toBe(true); });
  test("candidate → active (fast path)", () => { expect(isAllowedTransition("candidate", "active")).toBe(true); });
  test("candidate → canary (fast path)", () => { expect(isAllowedTransition("candidate", "canary")).toBe(true); });
  test("active → deprecated (displaced)", () => { expect(isAllowedTransition("active", "deprecated")).toBe(true); });
});

describe("isAllowedTransition — rollback and disable", () => {
  test("shadow → candidate (rollback)", () => { expect(isAllowedTransition("shadow", "candidate")).toBe(true); });
  test("canary → candidate (rollback)", () => { expect(isAllowedTransition("canary", "candidate")).toBe(true); });
  test("active → candidate (rollback)", () => { expect(isAllowedTransition("active", "candidate")).toBe(true); });
  test("any → disabled: candidate", () => { expect(isAllowedTransition("candidate", "disabled")).toBe(true); });
  test("any → disabled: active", () => { expect(isAllowedTransition("active", "disabled")).toBe(true); });
  test("disabled → candidate (restore)", () => { expect(isAllowedTransition("disabled", "candidate")).toBe(true); });
  test("deprecated → candidate (restore)", () => { expect(isAllowedTransition("deprecated", "candidate")).toBe(true); });
});

describe("isAllowedTransition — rejected", () => {
  test("self-loop rejected (candidate → candidate)", () => {
    expect(isAllowedTransition("candidate", "candidate")).toBe(false);
  });
  test("deprecated → active rejected (no direct reanimation)", () => {
    expect(isAllowedTransition("deprecated", "active")).toBe(false);
  });
  test("deprecated → deprecated rejected", () => {
    expect(isAllowedTransition("deprecated", "deprecated")).toBe(false);
  });
  test("shadow → deprecated rejected (only active → deprecated)", () => {
    expect(isAllowedTransition("shadow", "deprecated")).toBe(false);
  });
});

describe("nextStatesFrom", () => {
  test("returns a non-empty list for every state except deprecated-terminal-ish", () => {
    for (const s of ACTIVATION_STATES) {
      const next = nextStatesFrom(s);
      // every state has at least one next state (even deprecated can go to candidate)
      expect(next.length).toBeGreaterThan(0);
    }
  });

  test("returned states are consistent with isAllowedTransition", () => {
    for (const s of ACTIVATION_STATES) {
      for (const next of nextStatesFrom(s)) {
        expect(isAllowedTransition(s, next)).toBe(true);
      }
    }
  });
});

describe("P5 (property): no appendPromotionEvent yields a history with a disallowed (from,to) pair", () => {
  test("random valid transitions appended in sequence produce only allowed pairs", () => {
    fc.assert(
      fc.property(
        fc.array(fc.integer({ min: 0, max: 100 }), { minLength: 0, maxLength: 20 }),
        (seedIndexes) => {
          let artifact = createArtifact({
            actuatorType: "prompt-patch",
            scenario: "grid_ctf",
            payloadHash: "sha256:" + "f".repeat(64),
            provenance: anyProvenance,
          });
          let t = 0;
          for (const seedIdx of seedIndexes) {
            const options = nextStatesFrom(artifact.activationState);
            if (options.length === 0) break;
            const to = options[seedIdx % options.length];
            const event = createPromotionEvent({
              from: artifact.activationState,
              to,
              reason: `transition t=${t++}`,
              timestamp: new Date(Date.parse("2026-04-17T00:00:00Z") + t * 1000).toISOString(),
            });
            artifact = appendPromotionEvent(artifact, event);
          }
          // Every recorded event must be an allowed pair.
          for (const e of artifact.promotionHistory) {
            expect(isAllowedTransition(e.from, e.to)).toBe(true);
          }
        },
      ),
      { numRuns: 200 },
    );
  });

  test("appendPromotionEvent REJECTS any disallowed (from,to) pair", () => {
    fc.assert(
      fc.property(
        fc.constantFrom<ActivationState>(...ACTIVATION_STATES),
        fc.constantFrom<ActivationState>(...ACTIVATION_STATES),
        (from, to) => {
          // skip transitions the factory would reject on mismatched `from`
          let artifact = createArtifact({
            actuatorType: "prompt-patch",
            scenario: "grid_ctf",
            payloadHash: "sha256:" + "f".repeat(64),
            provenance: anyProvenance,
          });
          // Drive artifact into the `from` state via an allowed path, if possible.
          if (from !== artifact.activationState) {
            // Walk to `from` via shortest valid path — if no path exists, skip.
            const path = shortestPath(artifact.activationState, from);
            if (!path) return true;
            let t = 0;
            for (const state of path.slice(1)) {
              artifact = appendPromotionEvent(artifact, createPromotionEvent({
                from: artifact.activationState,
                to: state,
                reason: `setup t=${t++}`,
                timestamp: new Date(Date.parse("2026-04-16T00:00:00Z") + t * 1000).toISOString(),
              }));
            }
          }
          const event = createPromotionEvent({
            from,
            to,
            reason: "attempt",
            timestamp: "2026-04-17T23:00:00.000Z",
          });
          if (isAllowedTransition(from, to)) {
            expect(() => appendPromotionEvent(artifact, event)).not.toThrow();
          } else {
            expect(() => appendPromotionEvent(artifact, event)).toThrow();
          }
          return true;
        },
      ),
      { numRuns: 200 },
    );
  });
});

function shortestPath(start: ActivationState, goal: ActivationState): ActivationState[] | null {
  if (start === goal) return [start];
  const visited = new Set<ActivationState>([start]);
  const queue: { s: ActivationState; path: ActivationState[] }[] = [{ s: start, path: [start] }];
  while (queue.length) {
    const { s, path } = queue.shift()!;
    for (const next of nextStatesFrom(s)) {
      if (next === goal) return [...path, next];
      if (!visited.has(next)) {
        visited.add(next);
        queue.push({ s: next, path: [...path, next] });
      }
    }
  }
  return null;
}
