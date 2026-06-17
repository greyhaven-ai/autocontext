import { mkdtempSync, readFileSync, rmSync } from "node:fs";
import { tmpdir } from "node:os";
import { join } from "node:path";
import { describe, expect, it } from "vitest";

import {
  buildNegativeResultLedger,
  parseNegativeResultLedger,
  renderNegativeResultLessons,
  type NegativeResultEventInput,
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

describe("negative result ledger", () => {
  it("matches the shared Python/TypeScript parity fixture", () => {
    for (const item of fixture.cases) {
      expect(
        buildNegativeResultLedger({
          runId: item.run_id,
          generatedAt: item.generated_at,
          events: item.events,
        }),
      ).toEqual(item.expected_ledger);
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
        "Hard ban:",
      );
    } finally {
      rmSync(root, { recursive: true, force: true });
    }
  });

  it("parses durable ledger JSON and rejects schema-invalid data", () => {
    const ledger = fixture.cases[0]!.expected_ledger;
    const entry = ledger.entries[0]!;

    expect(parseNegativeResultLedger(ledger)).toEqual(ledger);
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
    expect(renderNegativeResultLessons(hardBan)).toContain("Hard ban:");
    expect(renderNegativeResultLessons(hardBan)).toContain("evt-hard-1");
    expect(renderNegativeResultLessons(hardBan)).toContain("evt-hard-2");
  });
});
