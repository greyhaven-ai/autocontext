import { describe, test, expect } from "vitest";
import * as fc from "fast-check";
import { mkdtempSync, rmSync, mkdirSync, writeFileSync } from "node:fs";
import { tmpdir } from "node:os";
import { join } from "node:path";
import { openRegistry } from "../../../src/control-plane/registry/index.js";
import { listStatePointers } from "../../../src/control-plane/registry/state-pointer.js";
import { createArtifact, createPromotionEvent } from "../../../src/control-plane/contract/factories.js";
import { hashDirectory } from "../../../src/control-plane/registry/content-address.js";
import { isAllowedTransition } from "../../../src/control-plane/promotion/transitions.js";
import { validateAppendOnly } from "../../../src/control-plane/contract/invariants.js";
import {
  artifactDirectory,
  listArtifactIds,
  loadArtifact,
} from "../../../src/control-plane/registry/artifact-store.js";
import { readHistory } from "../../../src/control-plane/registry/history-store.js";
import type { ContentHash, EnvironmentTag, Scenario } from "../../../src/control-plane/contract/branded-ids.js";
import type { ActivationState, Provenance } from "../../../src/control-plane/contract/types.js";

const aProvenance: Provenance = {
  authorType: "human",
  authorId: "jay@greyhaven.ai",
  parentArtifactIds: [],
  createdAt: "2026-04-17T12:00:00.000Z",
};

function tempPayload(parent: string, content: string): { dir: string; hash: ContentHash } {
  const dir = join(parent, "src-" + Math.random().toString(36).slice(2));
  mkdirSync(dir, { recursive: true });
  writeFileSync(join(dir, "f.txt"), content);
  return { dir, hash: hashDirectory(dir) };
}

describe("registry properties", () => {
  test("P1: at most one artifact per (scenario, actuatorType, environmentTag) is in active state after any sequence of valid promotions", () => {
    fc.assert(
      fc.property(
        fc.array(
          fc.record({
            scenarioIdx: fc.integer({ min: 0, max: 2 }),
            envIdx: fc.integer({ min: 0, max: 1 }),
            target: fc.constantFrom<ActivationState>("shadow", "canary", "active"),
          }),
          { minLength: 1, maxLength: 8 },
        ),
        (specs) => {
          const registryRoot = mkdtempSync(join(tmpdir(), "autocontext-prop-"));
          try {
            const reg = openRegistry(registryRoot);
            const scenarios: Scenario[] = ["grid_ctf", "othello", "alphacode"] as Scenario[];
            const envs: EnvironmentTag[] = ["production", "staging"] as EnvironmentTag[];
            let ts = 0;
            const stamp = (): string => new Date(Date.UTC(2026, 3, 17, 12, 0, ts++)).toISOString();
            for (const spec of specs) {
              const scenario = scenarios[spec.scenarioIdx];
              const env = envs[spec.envIdx];
              const { dir, hash } = tempPayload(registryRoot, `s${ts}`);
              const a = createArtifact({
                actuatorType: "prompt-patch",
                scenario,
                environmentTag: env,
                payloadHash: hash,
                provenance: aProvenance,
              });
              reg.saveArtifact(a, dir);
              reg.appendPromotionEvent(a.id, createPromotionEvent({
                from: "candidate",
                to: spec.target,
                reason: "prop test",
                timestamp: stamp(),
              }));
            }
            // Property: across ALL artifacts, at most ONE is currently in active per tuple.
            const counts = new Map<string, number>();
            const pointers = listStatePointers(registryRoot);
            const ptrKeys = new Set(pointers.map((p) => `${p.scenario}|${p.actuatorType}|${p.environmentTag}`));
            const ids = listArtifactIds(registryRoot);
            for (const id of ids) {
              const art = loadArtifact(registryRoot, id);
              if (art.activationState !== "active") continue;
              const key = `${art.scenario}|${art.actuatorType}|${art.environmentTag}`;
              counts.set(key, (counts.get(key) ?? 0) + 1);
            }
            for (const [key, n] of counts) {
              expect(n, `more than one active artifact for ${key}`).toBeLessThanOrEqual(1);
              expect(ptrKeys.has(key), `tuple ${key} has active artifact but no state pointer`).toBe(true);
            }
          } finally {
            rmSync(registryRoot, { recursive: true, force: true });
          }
        },
      ),
      { numRuns: 12 },
    );
  });

  test("P2: after any valid sequence of appendPromotionEvent calls, every artifact's on-disk history is a monotonic extension of its prior state", () => {
    fc.assert(
      fc.property(
        fc.array(
          fc.record({
            target: fc.constantFrom<ActivationState>("shadow", "canary", "active", "disabled"),
          }),
          { minLength: 1, maxLength: 6 },
        ),
        (steps) => {
          const registryRoot = mkdtempSync(join(tmpdir(), "autocontext-prop2-"));
          try {
            const reg = openRegistry(registryRoot);
            const { dir, hash } = tempPayload(registryRoot, "vP2");
            const a = createArtifact({
              actuatorType: "prompt-patch",
              scenario: "grid_ctf" as Scenario,
              payloadHash: hash,
              provenance: aProvenance,
            });
            reg.saveArtifact(a, dir);

            const snapshots: string[][] = [];
            let ts = 0;
            let current: ActivationState = "candidate";
            const historyPath = join(artifactDirectory(registryRoot, a.id), "promotion-history.jsonl");

            for (const step of steps) {
              if (!isAllowedTransition(current, step.target)) continue;
              const event = createPromotionEvent({
                from: current,
                to: step.target,
                reason: "p2",
                timestamp: new Date(Date.UTC(2026, 3, 17, 12, 0, ts++)).toISOString(),
              });
              reg.appendPromotionEvent(a.id, event);
              current = step.target;
              const onDisk = readHistory(historyPath);
              snapshots.push(onDisk.map((e) => `${e.from}->${e.to}@${e.timestamp}`));
            }

            // Each snapshot must be a prefix of every later snapshot.
            for (let i = 0; i < snapshots.length; i++) {
              for (let j = i + 1; j < snapshots.length; j++) {
                const earlier = snapshots[i];
                const later = snapshots[j];
                expect(later.length).toBeGreaterThanOrEqual(earlier.length);
                for (let k = 0; k < earlier.length; k++) {
                  expect(later[k]).toBe(earlier[k]);
                }
              }
            }

            // Final history must satisfy validateAppendOnly trivially.
            const final = readHistory(historyPath);
            const r = validateAppendOnly([], final);
            expect(r.valid).toBe(true);
          } finally {
            rmSync(registryRoot, { recursive: true, force: true });
          }
        },
      ),
      { numRuns: 12 },
    );
  });
});
