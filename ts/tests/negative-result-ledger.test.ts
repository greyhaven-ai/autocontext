import { mkdtempSync, readFileSync, rmSync } from "node:fs";
import { tmpdir } from "node:os";
import { join } from "node:path";
import { describe, expect, it } from "vitest";

import {
  buildNegativeResultLedger,
  evaluateNegativeResultApplicability,
  linkNegativeResultRetest,
  parseNegativeResultLedger,
  renderNegativeResultLessons,
  type NegativeResultEventInput,
  type NegativeResultApplicability,
  type NegativeResultApplicabilityContext,
  type NegativeResultEntry,
  type NegativeResultLedger,
} from "../src/analytics/negative-result-ledger.js";
import {
  readLatestNegativeResultLedgersMarkdown,
  readNegativeResultLedger,
  writeNegativeResultLedger,
} from "../src/knowledge/negative-result-ledger-store.js";

const fixture = JSON.parse(
  readFileSync(
    join(import.meta.dirname, "..", "..", "docs", "negative-result-ledger-parity-fixture.json"),
    "utf-8",
  ),
) as {
  cases: Array<{
    name: string;
    run_id: string;
    generated_at: string;
    events: NegativeResultEventInput[];
    expected_ledger: NegativeResultLedger;
  }>;
};

const applicabilityFixture = JSON.parse(
  readFileSync(
    join(import.meta.dirname, "..", "..", "docs", "negative-result-applicability-parity-fixture.json"),
    "utf-8",
  ),
) as {
  ledger: NegativeResultLedger;
  contexts: Record<string, NegativeResultApplicabilityContext>;
  decisions: Array<{
    result_id: string;
    context: string;
    expected: NegativeResultApplicability;
  }>;
  successful_retest: {
    original_result_id: string;
    entry: NegativeResultEntry;
    expected_superseded_by_result_id: string;
  };
};

const roundingFixture = JSON.parse(
  readFileSync(join(import.meta.dirname, "..", "..", "fixtures", "numeric-rounding-parity.json"), "utf-8"),
) as {
  cases: Array<{ name: string; value: number; expected: number }>;
};

const idFixture = JSON.parse(
  readFileSync(join(import.meta.dirname, "..", "..", "fixtures", "negative-result-id-parity.json"), "utf-8"),
) as {
  events: NegativeResultEventInput[];
  expected_result_ids: string[];
};

describe("negative result ledger", () => {
  it("matches the shared Python/TypeScript parity fixture", () => {
    for (const item of fixture.cases) {
      expect(
        buildNegativeResultLedger({
          runId: item.run_id,
          generatedAt: item.generated_at,
          events: item.events,
          scenarioName: "legacy-fixture",
          contextBundleDigest: "sha256:fixture",
          evaluatorEpoch: "fixture-eval",
        }),
      ).toMatchObject({
        schema_version: 2,
        run_id: item.run_id,
        entries: item.expected_ledger.entries.map((entry) => ({
          result_id: entry.result_id,
          applicability_scope: "exact_bundle",
        })),
      });
    }
  });

  it("uses shared content-derived IDs for idless events and retests only one result", () => {
    const defaults = {
      scenarioName: "grid_ctf",
      contextBundleDigest: "sha256:bundle-a",
      evaluatorEpoch: "eval-7",
    };
    const ledger = buildNegativeResultLedger({
      ...defaults,
      runId: "run-idless",
      generatedAt: "2026-08-17T12:00:00Z",
      events: idFixture.events,
    });

    const resultIds = ledger.entries.map((entry) => entry.result_id);
    expect(resultIds).toEqual(idFixture.expected_result_ids);
    expect(new Set(resultIds).size).toBe(resultIds.length);
    expect(buildNegativeResultLedger({
      ...defaults,
      runId: "run-idless",
      generatedAt: "2026-08-17T12:00:00Z",
      events: idFixture.events,
    }).entries.map((entry) => entry.result_id)).toEqual(resultIds);

    const retest = buildNegativeResultLedger({
      ...defaults,
      runId: "run-idless-retest",
      generatedAt: "2026-08-18T12:00:00Z",
      events: [{
        event_type: "candidate_rejected",
        timestamp: "2026-08-18T10:00:00Z",
        branch_id: "aa-retest",
        payload: {
          result_id: "retest-first-idless-result",
          disposition: "caution",
          reason: "Controlled replay did not reproduce the first failure.",
          retest_of_result_id: resultIds[0],
          retest_outcome: "not_reproduced",
          evaluated_seeds: ["seed-1"],
          evidence_refs: [{ uri: "retest.json", summary: "Controlled replay passed." }],
        },
      }],
    }).entries[0]!;
    const updated = linkNegativeResultRetest(ledger, resultIds[0]!, retest);

    expect(updated.entries[0]!.superseded_by_result_id).toBe(retest.result_id);
    expect(updated.entries.slice(1, 3).every((entry) => entry.superseded_by_result_id === null)).toBe(true);
  });

  it("rejects duplicate result IDs on build, parse, and link surfaces", () => {
    const defaults = {
      scenarioName: "grid_ctf",
      contextBundleDigest: "sha256:bundle-a",
      evaluatorEpoch: "eval-7",
    };
    const duplicateEvents = ["branch-a", "branch-b"].map((branchId): NegativeResultEventInput => ({
      event_id: "duplicate-negative-result",
      event_type: "branch_rejected",
      branch_id: branchId,
      payload: { reason: `Failure on ${branchId}.` },
    }));
    expect(() => buildNegativeResultLedger({
      ...defaults,
      runId: "run-duplicate-build",
      generatedAt: "2026-08-17T12:00:00Z",
      events: duplicateEvents,
    })).toThrow(/duplicate negative result ID/);

    const ledger = buildNegativeResultLedger({
      ...defaults,
      runId: "run-valid",
      generatedAt: "2026-08-17T12:00:00Z",
      events: idFixture.events,
    });
    const duplicateEntries = [ledger.entries[0]!, ledger.entries[0]!];
    expect(() => parseNegativeResultLedger({ ...ledger, entries: duplicateEntries })).toThrow(
      /duplicate negative result ID/,
    );

    const malformedLedger = { ...ledger, entries: duplicateEntries };
    expect(() => linkNegativeResultRetest(
      malformedLedger,
      ledger.entries[0]!.result_id,
      ledger.entries[1]!,
    )).toThrow(/duplicate negative result ID/);
  });

  it("persists negative result ledgers through the artifact store", () => {
    const root = mkdtempSync(join(tmpdir(), "negative-ledger-"));
    try {
      const knowledgeRoot = join(root, "knowledge");
      const ledger = parseNegativeResultLedger(fixture.cases[2]!.expected_ledger);

      writeNegativeResultLedger(knowledgeRoot, "grid_ctf", ledger.run_id, ledger);

      expect(readNegativeResultLedger(knowledgeRoot, "grid_ctf", ledger.run_id)).toEqual(ledger);
      expect(readLatestNegativeResultLedgersMarkdown(knowledgeRoot, "grid_ctf")).toContain(
        "legacy evidence has unknown context",
      );
    } finally {
      rmSync(root, { recursive: true, force: true });
    }
  });

  it("parses durable ledger JSON and rejects schema-invalid data", () => {
    const ledger = fixture.cases[0]!.expected_ledger;
    const entry = ledger.entries[0]!;

    expect(parseNegativeResultLedger(ledger)).toMatchObject({
      schema_version: 2,
      run_id: ledger.run_id,
      entries: [{ applicability_scope: "context_unknown" }],
    });
    expect(() => parseNegativeResultLedger({ ...ledger, surprise: true })).toThrow(
      /unexpected field/,
    );
    expect(() => parseNegativeResultLedger({ ...ledger, run_id: "" })).toThrow(/run_id/);
    expect(() =>
      parseNegativeResultLedger({ ...ledger, entries: [{ ...entry, disposition: "maybe" }] }),
    ).toThrow(/disposition/);
    const { branch_id: _branchId, ...missingBranch } = entry;
    expect(() => parseNegativeResultLedger({ ...ledger, entries: [missingBranch] })).toThrow(
      /missing field/,
    );
    expect(() =>
      parseNegativeResultLedger({ ...ledger, entries: [{ ...entry, generation_index: -1 }] }),
    ).toThrow(/generation_index/);
  });

  it("distinguishes cautionary lessons, noise, and hard bans", () => {
    const caution = parseNegativeResultLedger(fixture.cases[0]!.expected_ledger);
    const noise = parseNegativeResultLedger(fixture.cases[1]!.expected_ledger);
    const hardBan = parseNegativeResultLedger(fixture.cases[2]!.expected_ledger);

    expect(renderNegativeResultLessons(caution)).toContain("Caution:");
    expect(renderNegativeResultLessons(caution)).toContain("not a ban");
    expect(renderNegativeResultLessons(noise)).toBe("");
    expect(renderNegativeResultLessons(hardBan)).toContain("Caution:");
    expect(renderNegativeResultLessons(hardBan)).toContain("legacy evidence has unknown context");
    expect(renderNegativeResultLessons(hardBan)).toContain("evt-hard-1");
    expect(renderNegativeResultLessons(hardBan)).toContain("evt-hard-2");
  });

  it("scopes hard bans to matching context and makes stale evidence retestable", () => {
    const ledger = buildNegativeResultLedger({
      runId: "run-contextual",
      generatedAt: "2026-08-17T12:00:00Z",
      scenarioName: "grid_ctf",
      contextBundleDigest: "sha256:bundle-a",
      contextBundleFamily: "grid-family",
      evaluatorEpoch: "eval-7",
      verifierDigest: "sha256:verifier-a",
      trialCohort: "cohort-a",
      environmentFingerprint: "linux-amd64:v1",
      componentDependencies: [{ component_kind: "tool", key: "move", digest: "sha256:move-a" }],
      events: [{
        event_id: "neg-contextual",
        event_type: "branch_rejected",
        timestamp: "2026-08-17T11:00:00Z",
        branch_id: "branch-red",
        payload: {
          failure_kind: "unsafe_action",
          disposition: "hard_ban",
          reason: "Verifier rejected the action.",
          evidence_expires_at: "2026-09-01T00:00:00Z",
          evidence_refs: [{ uri: "evidence.json", summary: "Violation reproduced." }],
        },
      }],
    });
    const current = {
      scenario_name: "grid_ctf",
      context_bundle_digest: "sha256:bundle-a",
      context_bundle_family: "grid-family",
      evaluator_epoch: "eval-7",
      verifier_digest: "sha256:verifier-a",
      trial_cohort: "cohort-a",
      component_digests: { "tool:move": "sha256:move-a" },
      environment_fingerprint: "linux-amd64:v1",
      observed_at: "2026-08-17T12:00:00Z",
    };

    expect(evaluateNegativeResultApplicability(ledger.entries[0]!, current)).toMatchObject({
      state: "applicable",
      effective_disposition: "hard_ban",
    });
    expect(renderNegativeResultLessons(ledger, { applicabilityContext: current })).toContain("Hard ban:");

    const stale = { ...current, evaluator_epoch: "eval-8" };
    expect(evaluateNegativeResultApplicability(ledger.entries[0]!, stale)).toMatchObject({
      state: "retest_due",
      effective_disposition: "caution",
      reason: "evaluator epoch changed",
    });

    expect(evaluateNegativeResultApplicability(ledger.entries[0]!, {
      ...current,
      trial_cohort: "cohort-b",
    })).toMatchObject({
      state: "retest_due",
      effective_disposition: "caution",
      reason: "trial cohort changed",
    });
  });

  it("links a non-reproducing retest without erasing the original", () => {
    const defaults = {
      scenarioName: "grid_ctf",
      contextBundleDigest: "sha256:bundle-a",
      evaluatorEpoch: "eval-7",
      verifierDigest: "sha256:verifier-a",
      trialCohort: "cohort-a",
      componentDependencies: [{ component_kind: "tool", key: "move", digest: "sha256:move-a" }],
      environmentFingerprint: "linux-amd64:v1",
    };
    const original = buildNegativeResultLedger({
      ...defaults,
      runId: "run-retest",
      generatedAt: "2026-08-17T12:00:00Z",
      events: [{
        event_id: "neg-original",
        event_type: "branch_rejected",
        timestamp: "2026-08-17T11:00:00Z",
        branch_id: "branch-red",
        payload: {
          reason: "Old verifier rejected this branch.",
          disposition: "hard_ban",
          evidence_refs: [{ uri: "old.json", summary: "Old failure." }],
        },
      }],
    });
    const retest = buildNegativeResultLedger({
      ...defaults,
      runId: "run-retest-2",
      generatedAt: "2026-08-18T12:00:00Z",
      events: [{
        event_id: "neg-retest",
        event_type: "candidate_rejected",
        timestamp: "2026-08-18T11:00:00Z",
        branch_id: "branch-red-retest",
        payload: {
          reason: "Retest did not reproduce the failure.",
          retest_of_result_id: "neg-original",
          retest_outcome: "not_reproduced",
          disposition: "caution",
          evaluated_seeds: ["seed-1"],
          evidence_refs: [{ uri: "new.json", summary: "Replay passed." }],
        },
      }],
    }).entries[0]!;

    const updated = linkNegativeResultRetest(original, "neg-original", retest);

    expect(updated.entries.map((entry) => entry.result_id)).toEqual(["neg-original", "neg-retest"]);
    expect(updated.entries[0]!.superseded_by_result_id).toBe("neg-retest");
    expect(renderNegativeResultLessons(updated)).toContain("neg-retest");
    expect(renderNegativeResultLessons(updated)).not.toContain("Hard ban:");
  });

  it("does not let unrelated retests supersede negative results", () => {
    const defaults = {
      scenarioName: "grid_ctf",
      contextBundleDigest: "sha256:bundle-a",
      evaluatorEpoch: "eval-7",
      verifierDigest: "sha256:verifier-a",
      trialCohort: "cohort-a",
      componentDependencies: [{ component_kind: "tool", key: "move", digest: "sha256:move-a" }],
      environmentFingerprint: "linux-amd64:v1",
    };
    const original = buildNegativeResultLedger({
      ...defaults,
      runId: "run-hard-ban",
      generatedAt: "2026-08-17T12:00:00Z",
      events: [{
        event_id: "neg-hard-ban",
        event_type: "branch_rejected",
        timestamp: "2026-08-17T11:00:00Z",
        branch_id: "branch-red",
        payload: {
          disposition: "hard_ban",
          reason: "The active verifier rejected this branch.",
          evidence_refs: [{ uri: "old.json", summary: "Violation reproduced." }],
        },
      }],
    });
    const matchingRetest = buildNegativeResultLedger({
      ...defaults,
      runId: "run-retest",
      generatedAt: "2026-08-18T12:00:00Z",
      events: [{
        event_id: "neg-retest",
        event_type: "candidate_rejected",
        timestamp: "2026-08-18T11:00:00Z",
        branch_id: "branch-retest",
        payload: {
          disposition: "caution",
          reason: "The failure did not reproduce.",
          retest_of_result_id: "neg-hard-ban",
          retest_outcome: "not_reproduced",
          evaluated_seeds: ["seed-1"],
          evidence_refs: [{ uri: "new.json", summary: "Replay passed." }],
        },
      }],
    }).entries[0]!;

    const changedContexts = [
      { ...matchingRetest.context, context_bundle_digest: "sha256:bundle-b" },
      { ...matchingRetest.context, evaluator_epoch: "eval-8" },
      { ...matchingRetest.context, trial_cohort: "cohort-b" },
      {
        ...matchingRetest.context,
        component_dependencies: [{ component_kind: "tool", key: "move", digest: "sha256:move-b" }],
      },
    ];
    const caution: NegativeResultLedger = {
      ...original,
      entries: [{ ...original.entries[0]!, disposition: "caution" }],
    };
    for (const recorded of [original, caution]) {
      for (const context of changedContexts) {
        const updated = linkNegativeResultRetest(recorded, "neg-hard-ban", { ...matchingRetest, context });
        expect(updated.entries[0]!.superseded_by_result_id).toBeNull();
        expect(renderNegativeResultLessons(updated)).toContain("neg-hard-ban");
      }
    }
  });

  it("requires substantive later non-noise retest evidence under the same safety authority", () => {
    const defaults = {
      scenarioName: "grid_ctf",
      contextBundleDigest: "sha256:bundle-a",
      evaluatorEpoch: "eval-7",
    };
    const original = buildNegativeResultLedger({
      ...defaults,
      runId: "run-safety-original",
      generatedAt: "2026-08-17T12:00:00Z",
      events: [{
        event_id: "neg-safety",
        event_type: "branch_rejected",
        timestamp: "2026-08-17T11:00:00Z",
        branch_id: "branch-safety",
        payload: {
          disposition: "hard_ban",
          reason: "Safety verifier rejected this branch.",
          safety_policy_authority: "safety:v1",
          evidence_refs: [{ uri: "original.json", summary: "Violation reproduced." }],
        },
      }],
    });
    const retest = buildNegativeResultLedger({
      ...defaults,
      runId: "run-safety-retest",
      generatedAt: "2026-08-18T12:00:00Z",
      events: [{
        event_id: "neg-safety-retest",
        event_type: "candidate_rejected",
        timestamp: "2026-08-18T11:00:00Z",
        branch_id: "branch-safety-retest",
        payload: {
          disposition: "caution",
          reason: "Controlled replay did not reproduce the failure.",
          safety_policy_authority: "safety:v1",
          retest_of_result_id: "neg-safety",
          retest_outcome: "not_reproduced",
          evaluated_seeds: ["seed-1"],
          evidence_refs: [{ uri: "retest.json", summary: "Controlled replay passed." }],
        },
      }],
    }).entries[0]!;

    expect(linkNegativeResultRetest(original, "neg-safety", retest).entries[0]!.superseded_by_result_id)
      .toBe("neg-safety-retest");

    const invalidRetests: NegativeResultEntry[] = [
      { ...retest, evidence_refs: [] },
      { ...retest, evaluated_seeds: [], evaluated_probes: [] },
      { ...retest, occurred_at: "2026-08-16T11:00:00Z" },
      { ...retest, disposition: "noise" },
      { ...retest, safety_policy_authority: null },
      { ...retest, safety_policy_authority: "safety:v2" },
    ];
    for (const invalidRetest of invalidRetests) {
      expect(
        linkNegativeResultRetest(original, "neg-safety", invalidRetest).entries[0]!.superseded_by_result_id,
      ).toBeNull();
    }
  });

  it("normalizes naive and aware ISO timestamps for expiry", () => {
    const ledger = buildNegativeResultLedger({
      runId: "run-expiry",
      generatedAt: "2026-08-17T12:00:00Z",
      scenarioName: "grid_ctf",
      contextBundleDigest: "sha256:bundle-a",
      evaluatorEpoch: "eval-7",
      events: [{
        event_id: "neg-expiry",
        event_type: "branch_rejected",
        timestamp: "2026-08-17T11:00:00Z",
        branch_id: "branch-expiry",
        payload: {
          reason: "Time-limited negative result.",
          evidence_expires_at: "2026-08-18T00:00:00Z",
          evidence_refs: [{ uri: "expiry.json", summary: "Failure reproduced." }],
        },
      }],
    });
    const current: NegativeResultApplicabilityContext = {
      scenario_name: "grid_ctf",
      context_bundle_digest: "sha256:bundle-a",
      evaluator_epoch: "eval-7",
      observed_at: "2026-08-18T00:00:00",
    };

    expect(evaluateNegativeResultApplicability(ledger.entries[0]!, current).state).toBe("retest_due");
    const naiveExpiry = { ...ledger.entries[0]!, evidence_expires_at: "2026-08-18T00:00:00" };
    expect(evaluateNegativeResultApplicability(naiveExpiry, {
      ...current,
      observed_at: "2026-08-18T00:00:00+00:00",
    }).state).toBe("retest_due");
  });

  it("uses shared half-away-from-zero rounding for score deltas", () => {
    for (const fixtureCase of roundingFixture.cases) {
      const ledger = buildNegativeResultLedger({
        runId: `rounding-${fixtureCase.name}`,
        generatedAt: "2026-08-17T12:00:00Z",
        scenarioName: "grid_ctf",
        contextBundleDigest: "sha256:bundle-a",
        evaluatorEpoch: "eval-7",
        events: [
          {
            event_id: `${fixtureCase.name}-explicit`,
            event_type: "branch_rejected",
            branch_id: "branch-explicit",
            payload: { reason: "Explicit delta.", score_delta: fixtureCase.value },
          },
          {
            event_id: `${fixtureCase.name}-derived`,
            event_type: "branch_rejected",
            branch_id: "branch-derived",
            payload: { reason: "Derived delta.", score: fixtureCase.value, baseline_score: 0 },
          },
        ],
      });
      expect(ledger.entries.map((entry) => entry.score_delta)).toEqual([
        fixtureCase.expected,
        fixtureCase.expected,
      ]);
    }
  });

  it.each([Number.NaN, Number.POSITIVE_INFINITY, Number.NEGATIVE_INFINITY])(
    "ignores non-finite score delta input %s",
    (nonfinite) => {
      const ledger = buildNegativeResultLedger({
        runId: "nonfinite-delta",
        generatedAt: "2026-08-17T12:00:00Z",
        scenarioName: "grid_ctf",
        contextBundleDigest: "sha256:bundle-a",
        evaluatorEpoch: "eval-7",
        events: [
          {
            event_id: "nonfinite-explicit",
            event_type: "branch_rejected",
            branch_id: "branch-explicit",
            payload: { reason: "Explicit non-finite delta.", score_delta: nonfinite },
          },
          {
            event_id: "nonfinite-derived",
            event_type: "branch_rejected",
            branch_id: "branch-derived",
            payload: { reason: "Derived non-finite delta.", score: nonfinite, baseline_score: 0 },
          },
        ],
      });

      expect(ledger.entries.map((entry) => entry.score_delta)).toEqual([null, null]);
      const persisted = structuredClone(fixture.cases[0]!.expected_ledger);
      persisted.entries[0]!.score_delta = nonfinite;
      expect(() => parseNegativeResultLedger(persisted)).toThrow(/score_delta must be a number/);
    },
  );

  it("rejects a retest result ID that already exists in the ledger", () => {
    const defaults = {
      scenarioName: "grid_ctf",
      contextBundleDigest: "sha256:bundle-a",
      evaluatorEpoch: "eval-7",
    };
    const ledger = buildNegativeResultLedger({
      ...defaults,
      runId: "run-original",
      generatedAt: "2026-08-17T12:00:00Z",
      events: [{
        event_id: "duplicate-result",
        event_type: "branch_rejected",
        branch_id: "branch-original",
        payload: { reason: "Original failure." },
      }],
    });
    const duplicateRetest = buildNegativeResultLedger({
      ...defaults,
      runId: "run-retest",
      generatedAt: "2026-08-18T12:00:00Z",
      events: [{
        event_id: "duplicate-result",
        event_type: "candidate_rejected",
        branch_id: "branch-retest",
        payload: {
          reason: "Failure did not reproduce.",
          retest_of_result_id: "duplicate-result",
          retest_outcome: "not_reproduced",
        },
      }],
    }).entries[0]!;

    expect(() => linkNegativeResultRetest(ledger, "duplicate-result", duplicateRetest)).toThrow(
      "retest result already exists: duplicate-result",
    );
  });

  it("filters superseded entries before applying the render limit", () => {
    const ledger = buildNegativeResultLedger({
      runId: "run-render-limit",
      generatedAt: "2026-08-17T12:00:00Z",
      scenarioName: "grid_ctf",
      contextBundleDigest: "sha256:bundle-a",
      evaluatorEpoch: "eval-7",
      events: ["a-superseded", "b-active"].map((resultId) => ({
        event_id: resultId,
        event_type: "branch_rejected",
        branch_id: `branch-${resultId}`,
        payload: {
          disposition: "hard_ban",
          reason: `Failure ${resultId}.`,
          evidence_refs: [{ uri: `${resultId}.json`, summary: "Violation reproduced." }],
        },
      })),
    });
    ledger.entries[0] = { ...ledger.entries[0]!, superseded_by_result_id: "a-retest" };

    const rendered = renderNegativeResultLessons(ledger, { maxEntries: 1 });

    expect(rendered).toContain("b-active");
    expect(rendered).not.toContain("a-superseded");
  });

  it("matches the shared applicability and retest fixture", () => {
    const ledger = parseNegativeResultLedger(applicabilityFixture.ledger);
    const entries = new Map(ledger.entries.map((entry) => [entry.result_id, entry]));

    for (const item of applicabilityFixture.decisions) {
      expect(
        evaluateNegativeResultApplicability(entries.get(item.result_id)!, applicabilityFixture.contexts[item.context]),
      ).toEqual(item.expected);
    }

    const retest = applicabilityFixture.successful_retest;
    const updated = linkNegativeResultRetest(ledger, retest.original_result_id, retest.entry);
    expect(entries.has(retest.original_result_id)).toBe(true);
    expect(
      updated.entries.find((entry) => entry.result_id === retest.original_result_id)?.superseded_by_result_id,
    ).toBe(retest.expected_superseded_by_result_id);
  });
});
