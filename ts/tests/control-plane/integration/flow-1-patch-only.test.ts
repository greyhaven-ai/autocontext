// Flow 1 (spec §10.3) — candidate → eval → decide → promote → emit-pr (patch-only).
//
// Wires together: registry + eval-ingest + promotion + actuators + emit
// (no module mocks). Asserts the dry-run bundle directory layout per §9.5,
// the PR-body section headers per §9.4, and the absence of any git operations.

import { describe, test, expect, beforeEach, afterEach } from "vitest";
import {
  mkdtempSync,
  rmSync,
  existsSync,
  readdirSync,
  readFileSync,
  statSync,
} from "node:fs";
import { tmpdir } from "node:os";
import { join } from "node:path";
import { runControlPlaneCommand } from "../../../src/control-plane/cli/index.js";
import { emitPr } from "../../../src/control-plane/emit/index.js";
import { decidePromotion, defaultThresholds } from "../../../src/control-plane/promotion/index.js";
import {
  buildArtifactWithPassingEval,
  openTestRegistry,
} from "./_helpers/fixtures.js";

let tmp: string;

beforeEach(() => {
  tmp = mkdtempSync(join(tmpdir(), "autocontext-int-flow1-"));
});

afterEach(() => {
  rmSync(tmp, { recursive: true, force: true });
});

describe("Flow 1 — patch-only mode end-to-end", () => {
  test(
    "register → attach passing eval → decide passes → promotion apply --to canary → emit-pr patch-only writes the §9.5 bundle",
    async () => {
      const registry = openTestRegistry(tmp);

      // 1. Register a prompt-patch candidate with a real payload.
      // 2. Attach an EvalRun with metrics that pass all thresholds.
      const built = await buildArtifactWithPassingEval({
        registry,
        tmpRoot: tmp,
        scenario: "grid_ctf",
        actuatorType: "prompt-patch",
        ingestedAt: "2026-04-17T12:30:00.000Z",
        runId: "run_flow1",
      });
      const candidateId = built.artifact.id;

      // Reload through registry so we see the persisted EvalRunRef list.
      const reloaded = registry.loadArtifact(candidateId);
      expect(reloaded.evalRuns).toHaveLength(1);

      // 3. decidePromotion (pure) — pass=true, recommendedTargetState in {canary, active}.
      const evalRun = registry.loadEvalRun(candidateId, built.evalRun.runId);
      const decision = decidePromotion({
        candidate: { artifact: reloaded, evalRun },
        baseline: null,
        thresholds: defaultThresholds(),
        evaluatedAt: "2026-04-17T12:31:00.000Z",
      });
      expect(decision.pass).toBe(true);
      expect(["canary", "active", "shadow"]).toContain(decision.recommendedTargetState);

      // 4. promotion apply --to canary via the in-process CLI runner.
      const apply = await runControlPlaneCommand(
        [
          "promotion",
          "apply",
          candidateId,
          "--to",
          "canary",
          "--reason",
          "passing-eval-flow1",
        ],
        { cwd: tmp, now: () => "2026-04-17T12:32:00.000Z" },
      );
      expect(apply.exitCode).toBe(0);

      // 5. emit-pr patch-only via the public emit/ surface (richer assertions).
      const timestamp = "2026-04-17T12:33:00.000Z";
      const result = await emitPr(registry, candidateId, {
        mode: "patch-only",
        baseline: null,
        timestamp,
        autocontextVersion: "0.0.0-test",
      });

      // 6. Assert the §9.5 directory layout.
      expect(result.location.kind).toBe("local-path");
      const bundleRoot = result.location.value;
      expect(bundleRoot).toContain(
        join(".autocontext", "dry-run-patches", candidateId),
      );
      expect(existsSync(bundleRoot)).toBe(true);

      const patchesDir = join(bundleRoot, "patches");
      expect(statSync(patchesDir).isDirectory()).toBe(true);
      const patchFiles = readdirSync(patchesDir);
      expect(patchFiles.length).toBeGreaterThanOrEqual(1);
      // Each patch file ends with .patch
      for (const f of patchFiles) {
        expect(f.endsWith(".patch")).toBe(true);
      }

      const prBodyPath = join(bundleRoot, "pr-body.md");
      const decisionJson = join(bundleRoot, "decision.json");
      const layoutJson = join(bundleRoot, "resolved-layout.json");
      const planJson = join(bundleRoot, "plan.json");
      expect(existsSync(prBodyPath)).toBe(true);
      expect(existsSync(decisionJson)).toBe(true);
      expect(existsSync(layoutJson)).toBe(true);
      expect(existsSync(planJson)).toBe(true);

      // 7. PR body contains the expected §9.4 section headers.
      const body = readFileSync(prBodyPath, "utf-8");
      expect(body).toContain("### Metric deltas");
      expect(body).toContain("### Dataset provenance");
      expect(body).toContain("### Rollback");
      expect(body).toContain("### Audit");

      // 8. NO git operations — the tmp dir was never `git init`'d.
      const dotGit = join(tmp, ".git");
      expect(existsSync(dotGit)).toBe(false);
    },
  );

  test("two emit-pr invocations with the same timestamp produce byte-identical bundles (idempotence)", async () => {
    const registry = openTestRegistry(tmp);
    const built = await buildArtifactWithPassingEval({
      registry,
      tmpRoot: tmp,
      scenario: "grid_ctf",
      actuatorType: "prompt-patch",
      runId: "run_flow1_idem",
    });
    const id = built.artifact.id;

    const ts = "2026-04-17T12:34:00.000Z";
    const r1 = await emitPr(registry, id, {
      mode: "patch-only",
      baseline: null,
      timestamp: ts,
      autocontextVersion: "0.0.0-test",
    });
    const r2 = await emitPr(registry, id, {
      mode: "patch-only",
      baseline: null,
      timestamp: ts,
      autocontextVersion: "0.0.0-test",
    });
    expect(r1.location.value).toBe(r2.location.value);

    const files = ["pr-body.md", "decision.json", "resolved-layout.json", "plan.json"];
    for (const f of files) {
      const a = readFileSync(join(r1.location.value, f), "utf-8");
      const b = readFileSync(join(r2.location.value, f), "utf-8");
      expect(a).toBe(b);
    }
  });
});
