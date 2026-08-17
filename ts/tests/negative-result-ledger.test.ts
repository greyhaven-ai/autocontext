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
  });

  it("links a non-reproducing retest without erasing the original", () => {
    const defaults = {
      scenarioName: "grid_ctf",
      contextBundleDigest: "sha256:bundle-a",
      evaluatorEpoch: "eval-7",
    };
    const original = buildNegativeResultLedger({
      ...defaults,
      runId: "run-retest",
      generatedAt: "2026-08-17T12:00:00Z",
      events: [{
        event_id: "neg-original",
        event_type: "branch_rejected",
        branch_id: "branch-red",
        payload: {
          reason: "Old verifier rejected this branch.",
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
        branch_id: "branch-red-retest",
        payload: {
          reason: "Retest did not reproduce the failure.",
          retest_of_result_id: "neg-original",
          retest_outcome: "not_reproduced",
          disposition: "noise",
          evidence_refs: [{ uri: "new.json", summary: "Replay passed." }],
        },
      }],
    }).entries[0]!;

    const updated = linkNegativeResultRetest(original, "neg-original", retest);

    expect(updated.entries.map((entry) => entry.result_id)).toEqual(["neg-original", "neg-retest"]);
    expect(updated.entries[0]!.superseded_by_result_id).toBe("neg-retest");
    expect(renderNegativeResultLessons(updated)).toBe("");
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
