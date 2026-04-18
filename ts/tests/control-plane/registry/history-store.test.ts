import { describe, test, expect, beforeEach, afterEach } from "vitest";
import {
  mkdtempSync,
  rmSync,
  readFileSync,
  writeFileSync,
  mkdirSync,
} from "node:fs";
import { tmpdir } from "node:os";
import { join } from "node:path";
import {
  appendHistory,
  readHistory,
} from "../../../src/control-plane/registry/history-store.js";
import type { PromotionEvent } from "../../../src/control-plane/contract/types.js";

const e = (n: number, from: PromotionEvent["from"] = "candidate", to: PromotionEvent["to"] = "shadow"): PromotionEvent => ({
  from,
  to,
  reason: `r${n}`,
  timestamp: `2026-04-17T12:0${n}:00.000Z`,
});

describe("history-store", () => {
  let dir: string;
  let path: string;

  beforeEach(() => {
    dir = mkdtempSync(join(tmpdir(), "autocontext-history-"));
    mkdirSync(join(dir, "art"), { recursive: true });
    path = join(dir, "art", "promotion-history.jsonl");
  });

  afterEach(() => {
    rmSync(dir, { recursive: true, force: true });
  });

  test("readHistory returns [] when the file does not exist", () => {
    expect(readHistory(path)).toEqual([]);
  });

  test("appendHistory writes a single line terminated with newline", () => {
    appendHistory(path, [], [e(1)]);
    const raw = readFileSync(path, "utf-8");
    expect(raw.endsWith("\n")).toBe(true);
    expect(raw.split("\n").filter(Boolean)).toHaveLength(1);
  });

  test("round-trip: events appended individually read back in order", () => {
    appendHistory(path, [], [e(1)]);
    appendHistory(path, [e(1)], [e(1), e(2)]);
    appendHistory(path, [e(1), e(2)], [e(1), e(2), e(3)]);
    const back = readHistory(path);
    expect(back).toEqual([e(1), e(2), e(3)]);
  });

  test("appendHistory refuses if prev does not match the on-disk prefix (tampering)", () => {
    appendHistory(path, [], [e(1)]);
    appendHistory(path, [e(1)], [e(1), e(2)]);

    // Tamper: rewrite line 1 to a different reason.
    writeFileSync(path, JSON.stringify({ ...e(1), reason: "tampered" }) + "\n" + JSON.stringify(e(2)) + "\n");

    expect(() => appendHistory(path, [e(1), e(2)], [e(1), e(2), e(3)])).toThrow(/append.*only|tamper|mismatch/i);
  });

  test("appendHistory refuses when next is not an extension of prev", () => {
    appendHistory(path, [], [e(1)]);
    expect(() => appendHistory(path, [e(1)], [e(2)])).toThrow();
  });

  test("readHistory throws if the file ends without a trailing newline (partial write)", () => {
    writeFileSync(path, JSON.stringify(e(1)) + "\n" + JSON.stringify(e(2))); // no trailing \n
    expect(() => readHistory(path)).toThrow(/partial|trailing|newline/i);
  });

  test("readHistory parses only well-formed lines and reports invalid JSON", () => {
    writeFileSync(path, "not-json\n");
    expect(() => readHistory(path)).toThrow();
  });

  test("appendHistory writes exactly the new tail (does not rewrite the file)", () => {
    appendHistory(path, [], [e(1)]);
    const before = readFileSync(path, "utf-8");
    appendHistory(path, [e(1)], [e(1), e(2)]);
    const after = readFileSync(path, "utf-8");
    expect(after.startsWith(before)).toBe(true);
  });
});
