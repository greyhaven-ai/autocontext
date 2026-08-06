/**
 * AC-898: Typed harness entries with scoped CRUD edits and rollback.
 *
 * Mirrors Python's tests/test_harness_entries.py.
 */

import { describe, it, expect, beforeEach, afterEach } from "vitest";
import {
  appendFileSync,
  existsSync,
  mkdirSync,
  readdirSync,
  readFileSync,
  writeFileSync,
} from "node:fs";
import { mkdtempSync, rmSync } from "node:fs";
import { join } from "node:path";
import { tmpdir } from "node:os";
import {
  HarnessEntryStore,
  SCOPE_ORDER,
  type HarnessEdit,
} from "../src/knowledge/harness-entries.js";

let root: string;

beforeEach(() => {
  root = mkdtempSync(join(tmpdir(), "harness-"));
});

afterEach(() => {
  rmSync(root, { recursive: true, force: true });
});

function createEdit(overrides: Partial<HarnessEdit> = {}): HarnessEdit {
  return {
    action: "create",
    kind: "fact",
    id: "",
    title: "t",
    content: "c",
    expectedOutcome: "",
    reason: "",
    ...overrides,
  };
}

describe("models", () => {
  it("scope order is monotone", () => {
    expect(SCOPE_ORDER.run).toBeLessThan(SCOPE_ORDER.scenario_family);
    expect(SCOPE_ORDER.scenario_family).toBeLessThan(SCOPE_ORDER.global);
  });
});

describe("store crud", () => {
  it("create assigns id, scope, timestamps", () => {
    const store = new HarnessEntryStore(root, { nowIso: () => "T0" });
    const refinement = store.apply([createEdit()], {
      scope: "run",
      summary: "first",
      source: "run_123",
    });
    const applied = refinement.appliedEdits[0];
    expect(applied.applied).toBe(true);
    expect(applied.error).toBe("");
    const entry = store.entries()[0];
    expect(entry.id).toBe(applied.entryId);
    expect(entry.id.startsWith("harness_")).toBe(true);
    expect(entry.scope).toBe("run");
    expect(entry.source).toBe("run_123");
    expect(entry.createdAt).toBe("T0");
    expect(entry.updatedAt).toBe("T0");
    expect(entry.version).toBe(1);
  });

  it("update bumps version and keeps unset fields", () => {
    const store = new HarnessEntryStore(root, { nowIso: () => "T0" });
    const created = store.apply([createEdit({ kind: "policy", expectedOutcome: "e" })], {
      scope: "run",
    });
    const entryId = created.appliedEdits[0].entryId;
    store.apply(
      [createEdit({ action: "update", kind: "policy", id: entryId, title: "", content: "c2" })],
      { scope: "run" },
    );
    const entry = store.entries()[0];
    expect(entry.content).toBe("c2");
    expect(entry.title).toBe("t");
    expect(entry.expectedOutcome).toBe("e");
    expect(entry.version).toBe(2);
  });

  it("delete removes entry", () => {
    const store = new HarnessEntryStore(root);
    const created = store.apply([createEdit()], { scope: "run" });
    const entryId = created.appliedEdits[0].entryId;
    store.apply([createEdit({ action: "delete", id: entryId })], { scope: "run" });
    expect(store.entries()).toEqual([]);
  });

  it("duplicate create and missing update record errors", () => {
    const store = new HarnessEntryStore(root);
    store.apply([createEdit({ id: "harness_dup" })], { scope: "run" });
    const dup = store.apply([createEdit({ id: "harness_dup" })], { scope: "run" });
    expect(dup.appliedEdits[0].applied).toBe(false);
    expect(dup.appliedEdits[0].error).toBe("duplicate_id");
    const missing = store.apply(
      [createEdit({ action: "update", id: "harness_nope", content: "x" })],
      { scope: "run" },
    );
    expect(missing.appliedEdits[0].applied).toBe(false);
    expect(missing.appliedEdits[0].error).toBe("not_found");
  });

  it("entries filters by kind and scope", () => {
    const store = new HarnessEntryStore(root);
    store.apply([createEdit({ title: "f" })], { scope: "run" });
    store.apply([createEdit({ kind: "policy", title: "p" })], { scope: "global" });
    expect(store.entries({ kind: "policy" }).map((e) => e.kind)).toEqual(["policy"]);
    expect(store.entries({ scope: "global" }).map((e) => e.scope)).toEqual(["global"]);
  });
});

describe("persistence hardening", () => {
  it("corrupt state degrades to empty", () => {
    const store = new HarnessEntryStore(root);
    mkdirSync(root, { recursive: true });
    writeFileSync(store.statePath, "{not json", "utf8");
    expect(store.entries()).toEqual([]);
    store.apply([createEdit()], { scope: "run" });
    expect(store.entries()).toHaveLength(1);
  });

  it("state write leaves no temp files", () => {
    const store = new HarnessEntryStore(root);
    store.apply([createEdit()], { scope: "run" });
    const leftovers = readdirSync(root).filter((name) => name.endsWith(".tmp"));
    expect(leftovers).toEqual([]);
  });

  it("history appends and skips malformed lines", () => {
    const store = new HarnessEntryStore(root);
    store.apply([createEdit()], { scope: "run", summary: "one" });
    appendFileSync(store.historyPath, "{torn line\n", "utf8");
    store.apply([createEdit({ title: "t2", content: "c2" })], { scope: "run", summary: "two" });
    expect(store.loadHistory().map((r) => r.summary)).toEqual(["one", "two"]);
  });
});

describe("scope guardrails", () => {
  it("run refinement cannot edit global entry", () => {
    const store = new HarnessEntryStore(root);
    const created = store.apply([createEdit({ kind: "policy", title: "g" })], { scope: "global" });
    const entryId = created.appliedEdits[0].entryId;
    const update = store.apply(
      [createEdit({ action: "update", kind: "policy", id: entryId, content: "x" })],
      { scope: "run" },
    );
    expect(update.appliedEdits[0].applied).toBe(false);
    expect(update.appliedEdits[0].error).toBe("scope_readonly");
    const del = store.apply([createEdit({ action: "delete", kind: "policy", id: entryId })], {
      scope: "run",
    });
    expect(del.appliedEdits[0].applied).toBe(false);
    expect(del.appliedEdits[0].error).toBe("scope_readonly");
    expect(store.entries()[0].content).toBe("c");
  });

  it("global refinement can edit run entry", () => {
    const store = new HarnessEntryStore(root);
    const created = store.apply([createEdit({ title: "r" })], { scope: "run" });
    const entryId = created.appliedEdits[0].entryId;
    const update = store.apply([createEdit({ action: "update", id: entryId, content: "x" })], {
      scope: "global",
    });
    expect(update.appliedEdits[0].applied).toBe(true);
  });
});

describe("rollback", () => {
  it("inverts create, update, delete", () => {
    const store = new HarnessEntryStore(root);
    const base = store.apply(
      [
        createEdit({ id: "harness_keep", title: "keep", content: "v1" }),
        createEdit({ id: "harness_gone", title: "gone", content: "v1" }),
      ],
      { scope: "run" },
    );
    expect(base.appliedEdits.every((item) => item.applied)).toBe(true);
    const batch = store.apply(
      [
        createEdit({ kind: "policy", id: "harness_new", title: "new" }),
        createEdit({ action: "update", id: "harness_keep", content: "v2" }),
        createEdit({ action: "delete", id: "harness_gone" }),
      ],
      { scope: "run" },
    );
    const result = store.rollback(batch.id);
    expect(result.rollbackOf).toBe(batch.id);
    const byId = new Map(store.entries().map((entry) => [entry.id, entry]));
    expect([...byId.keys()].sort()).toEqual(["harness_gone", "harness_keep"]);
    expect(byId.get("harness_keep")?.content).toBe("v1");
    expect(byId.get("harness_gone")?.content).toBe("v1");
  });

  it("unknown id throws", () => {
    const store = new HarnessEntryStore(root);
    expect(() => store.rollback("refinement_nope")).toThrow(/unknown refinement/);
  });

  it("rollback is itself recorded", () => {
    const store = new HarnessEntryStore(root);
    const batch = store.apply([createEdit()], { scope: "run" });
    store.rollback(batch.id);
    const history = store.loadHistory();
    expect(history).toHaveLength(2);
    expect(history[1].rollbackOf).toBe(batch.id);
  });
});

describe("outcome and render", () => {
  it("markOutcome updates entry", () => {
    const store = new HarnessEntryStore(root, { nowIso: () => "T1" });
    const created = store.apply([createEdit({ kind: "policy" })], { scope: "run" });
    const entryId = created.appliedEdits[0].entryId;
    const marked = store.markOutcome(entryId, "refuted", {
      scope: "run",
      evidence: "score did not improve over 3 gens",
    });
    expect(marked.outcome).toBe("refuted");
    expect(marked.outcomeEvidence.startsWith("score did not")).toBe(true);
    expect(marked.version).toBe(2);
    expect(marked.updatedAt).toBe("T1");
  });

  it("markOutcome unknown id throws", () => {
    const store = new HarnessEntryStore(root);
    expect(() => store.markOutcome("harness_nope", "confirmed", { scope: "run" })).toThrow(
      /unknown harness entry/,
    );
  });

  it("renderMarkdown groups by kind and hides refuted", () => {
    const store = new HarnessEntryStore(root);
    store.apply(
      [
        createEdit({
          kind: "policy",
          id: "harness_p1",
          title: "P",
          content: "line1\nline2",
          expectedOutcome: "score rises",
        }),
        createEdit({ id: "harness_f1", title: "F", content: "fact" }),
        createEdit({ id: "harness_f2", title: "Bad", content: "wrong" }),
      ],
      { scope: "run" },
    );
    store.markOutcome("harness_f2", "refuted", { scope: "run" });
    const text = store.renderMarkdown();
    expect(text).toContain("## Harness Entries");
    expect(text).toContain("### Policies");
    expect(text).toContain("### Facts");
    expect(text).toContain("line1\n  line2");
    expect(text).toContain("(expected: score rises)");
    expect(text).not.toContain("Bad");
  });

  it("renderMarkdown on empty store is empty string", () => {
    expect(new HarnessEntryStore(root).renderMarkdown()).toBe("");
  });
});

describe("review hardening (parity with Python TestReviewHardening)", () => {
  it("markOutcome respects scope guardrail", () => {
    const store = new HarnessEntryStore(root);
    const created = store.apply([createEdit({ kind: "policy", title: "g" })], { scope: "global" });
    const entryId = created.appliedEdits[0].entryId;
    expect(() => store.markOutcome(entryId, "refuted", { scope: "run" })).toThrow(/scope_readonly/);
    const marked = store.markOutcome(entryId, "refuted", { scope: "global" });
    expect(marked.outcome).toBe("refuted");
  });

  it("markOutcome with a missing or unknown scope throws instead of bypassing the guardrail", () => {
    const store = new HarnessEntryStore(root);
    store.apply([createEdit({ kind: "policy", id: "harness_g", title: "g" })], { scope: "global" });
    expect(() =>
      // @ts-expect-error untyped callers can omit scope; the runtime check must catch it
      store.markOutcome("harness_g", "refuted", { evidence: "no scope" }),
    ).toThrow(/unknown harness scope/);
    expect(() =>
      // @ts-expect-error unknown scope strings must not compare as writable
      store.markOutcome("harness_g", "refuted", { scope: "galaxy" }),
    ).toThrow(/unknown harness scope/);
    expect(store.entries()[0].outcome).toBe("pending");
  });

  it("apply with a missing or unknown scope throws instead of bypassing the guardrail", () => {
    const store = new HarnessEntryStore(root);
    store.apply([createEdit({ kind: "policy", id: "harness_g", title: "g" })], { scope: "global" });
    const edit = createEdit({ action: "update", kind: "policy", id: "harness_g", content: "x" });
    // @ts-expect-error untyped callers can omit scope; the runtime check must catch it
    expect(() => store.apply([edit], {})).toThrow(/unknown harness scope/);
    // @ts-expect-error unknown scope strings must not compare as writable
    expect(() => store.apply([edit], { scope: "galaxy" })).toThrow(/unknown harness scope/);
    expect(store.entries()[0].content).toBe("c");
    expect(store.loadHistory()).toHaveLength(1);
  });

  it("rollback of a rollback restores the original", () => {
    const store = new HarnessEntryStore(root);
    store.apply([createEdit({ id: "harness_a", content: "v1" })], { scope: "run" });
    const batch = store.apply([createEdit({ action: "update", id: "harness_a", content: "v2" })], {
      scope: "run",
    });
    const first = store.rollback(batch.id);
    expect(store.entries()[0].content).toBe("v1");
    store.rollback(first.id);
    expect(store.entries()[0].content).toBe("v2");
  });

  it("rollback preserves a marked outcome", () => {
    const store = new HarnessEntryStore(root);
    store.apply([createEdit({ kind: "policy", id: "harness_p", content: "v1" })], { scope: "run" });
    const batch = store.apply(
      [createEdit({ action: "update", kind: "policy", id: "harness_p", content: "v2" })],
      { scope: "run" },
    );
    store.markOutcome("harness_p", "refuted", { scope: "run", evidence: "did not deliver" });
    store.rollback(batch.id);
    const entry = store.entries()[0];
    expect(entry.content).toBe("v1");
    expect(entry.outcome).toBe("refuted");
    expect(entry.outcomeEvidence).toBe("did not deliver");
    expect(store.renderMarkdown()).not.toContain("harness_p");
  });

  it("rollback lost-update semantics pinned", () => {
    const store = new HarnessEntryStore(root);
    store.apply([createEdit({ id: "harness_a", content: "v1" })], { scope: "run" });
    const mid = store.apply([createEdit({ action: "update", id: "harness_a", content: "v2" })], {
      scope: "run",
    });
    store.apply([createEdit({ action: "update", id: "harness_a", content: "v3" })], {
      scope: "run",
    });
    store.rollback(mid.id);
    expect(store.entries()[0].content).toBe("v1");
  });

  it("empty apply is a no-op", () => {
    const store = new HarnessEntryStore(root);
    const refinement = store.apply([], { scope: "run" });
    expect(refinement.appliedEdits).toEqual([]);
    expect(existsSync(store.statePath)).toBe(false);
    expect(store.loadHistory()).toEqual([]);
  });

  it("partial batch failure does not block others", () => {
    const store = new HarnessEntryStore(root);
    const batch = store.apply(
      [
        createEdit({ action: "update", id: "harness_nope", content: "x" }),
        createEdit({ id: "harness_ok" }),
      ],
      { scope: "run" },
    );
    expect(batch.appliedEdits.map((item) => item.applied)).toEqual([false, true]);
    expect(store.entries().map((entry) => entry.id)).toEqual(["harness_ok"]);
  });

  it("invalid entries in the state file are dropped on load", () => {
    const store = new HarnessEntryStore(root);
    store.apply([createEdit({ id: "harness_good" })], { scope: "run" });
    const raw = JSON.parse(readFileSync(store.statePath, "utf8"));
    raw.entries.harness_bad = { ...raw.entries.harness_good, id: "harness_bad", kind: "vibe" };
    writeFileSync(store.statePath, JSON.stringify(raw), "utf8");
    expect(store.entries().map((entry) => entry.id)).toEqual(["harness_good"]);
  });
});
