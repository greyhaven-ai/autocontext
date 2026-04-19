import { describe, test, expect, beforeEach, afterEach } from "vitest";
import { mkdtempSync, rmSync, mkdirSync, writeFileSync, existsSync, readFileSync } from "node:fs";
import { tmpdir } from "node:os";
import { join } from "node:path";
import {
  loadSeenIds,
  appendSeenId,
  rebuildSeenIdsFromIngested,
} from "../../../../src/production-traces/ingest/dedupe.js";
import { seenIdsPath, ingestedDir } from "../../../../src/production-traces/ingest/paths.js";
import {
  newProductionTraceId,
  parseProductionTraceId,
  type ProductionTraceId,
} from "../../../../src/production-traces/contract/branded-ids.js";

describe("dedupe seen-ids cache", () => {
  let dir: string;

  beforeEach(() => {
    dir = mkdtempSync(join(tmpdir(), "autocontext-dedupe-"));
  });

  afterEach(() => {
    rmSync(dir, { recursive: true, force: true });
  });

  test("loadSeenIds returns an empty set when the file does not exist", async () => {
    const seen = await loadSeenIds(dir);
    expect(seen.size).toBe(0);
  });

  test("appendSeenId writes one id per line and loadSeenIds reads them back", async () => {
    const id1 = newProductionTraceId();
    const id2 = newProductionTraceId();
    await appendSeenId(dir, id1);
    await appendSeenId(dir, id2);

    const seen = await loadSeenIds(dir);
    expect(seen.has(id1)).toBe(true);
    expect(seen.has(id2)).toBe(true);
    expect(seen.size).toBe(2);

    const raw = readFileSync(seenIdsPath(dir), "utf-8");
    expect(raw).toBe(`${id1}\n${id2}\n`);
  });

  test("loadSeenIds tolerates and skips blank lines", async () => {
    const id1 = newProductionTraceId();
    const id2 = newProductionTraceId();
    mkdirSync(join(dir, ".autocontext", "production-traces"), { recursive: true });
    writeFileSync(seenIdsPath(dir), `${id1}\n\n${id2}\n\n`);

    const seen = await loadSeenIds(dir);
    expect(seen.has(id1)).toBe(true);
    expect(seen.has(id2)).toBe(true);
    expect(seen.size).toBe(2);
  });

  test("loadSeenIds skips malformed (non-ULID) lines", async () => {
    const id1 = newProductionTraceId();
    mkdirSync(join(dir, ".autocontext", "production-traces"), { recursive: true });
    writeFileSync(seenIdsPath(dir), `${id1}\nnot-a-ulid\n`);

    const seen = await loadSeenIds(dir);
    expect(seen.has(id1)).toBe(true);
    expect(seen.size).toBe(1);
  });

  test("handles a large seen-ids file via streaming (10k+ entries)", async () => {
    const ids: ProductionTraceId[] = [];
    for (let i = 0; i < 10_000; i++) {
      ids.push(newProductionTraceId());
    }
    mkdirSync(join(dir, ".autocontext", "production-traces"), { recursive: true });
    writeFileSync(seenIdsPath(dir), ids.join("\n") + "\n");

    const seen = await loadSeenIds(dir);
    expect(seen.size).toBe(10_000);
    expect(seen.has(ids[0])).toBe(true);
    expect(seen.has(ids[9999])).toBe(true);
  });

  test("appendSeenId creates parent directories if missing", async () => {
    const id = newProductionTraceId();
    await appendSeenId(dir, id);
    expect(existsSync(seenIdsPath(dir))).toBe(true);
  });

  test("rebuildSeenIdsFromIngested walks ingested/* and recovers ids", async () => {
    // Create two date partitions, each with a jsonl file containing trace ids.
    const id1 = newProductionTraceId();
    const id2 = newProductionTraceId();
    const id3 = newProductionTraceId();

    const date1 = ingestedDir(dir, "2026-04-17");
    const date2 = ingestedDir(dir, "2026-04-18");
    mkdirSync(date1, { recursive: true });
    mkdirSync(date2, { recursive: true });

    // One trace per line with full schema payload (only traceId is read).
    const line = (id: ProductionTraceId) =>
      JSON.stringify({
        schemaVersion: "1.0",
        traceId: id,
        source: { emitter: "sdk", sdk: { name: "x", version: "0.0" } },
      });
    writeFileSync(join(date1, "batch-a.jsonl"), `${line(id1)}\n${line(id2)}\n`);
    writeFileSync(join(date2, "batch-b.jsonl"), `${line(id3)}\n`);

    // Non-jsonl files should be ignored.
    writeFileSync(join(date1, "batch-a.receipt.json"), "{}");

    const seen = await rebuildSeenIdsFromIngested(dir);
    expect(seen.size).toBe(3);
    expect(seen.has(id1)).toBe(true);
    expect(seen.has(id2)).toBe(true);
    expect(seen.has(id3)).toBe(true);
  });

  test("rebuildSeenIdsFromIngested returns an empty set when ingested/ does not exist", async () => {
    const seen = await rebuildSeenIdsFromIngested(dir);
    expect(seen.size).toBe(0);
  });

  test("rebuildSeenIdsFromIngested skips malformed lines without throwing", async () => {
    const id1 = newProductionTraceId();
    const date1 = ingestedDir(dir, "2026-04-17");
    mkdirSync(date1, { recursive: true });
    const good = JSON.stringify({ traceId: id1 });
    writeFileSync(join(date1, "batch.jsonl"), `${good}\nnot json\n{"traceId":"bad"}\n`);

    const seen = await rebuildSeenIdsFromIngested(dir);
    expect(seen.has(id1)).toBe(true);
    expect(seen.size).toBe(1);
  });

  test("parseProductionTraceId is the gate: invalid ULIDs are dropped", async () => {
    // Belt-and-suspenders: loadSeenIds uses parseProductionTraceId.
    mkdirSync(join(dir, ".autocontext", "production-traces"), { recursive: true });
    writeFileSync(seenIdsPath(dir), `AAAA\n${newProductionTraceId()}\n`);
    const seen = await loadSeenIds(dir);
    // Only the real ULID survives; "AAAA" is too short.
    expect(seen.size).toBe(1);
    // Sanity on the parser boundary.
    expect(parseProductionTraceId("AAAA")).toBe(null);
  });
});
