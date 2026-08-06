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
  type HarnessEntryKind,
  type HarnessScope,
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
    const result = store.rollback(batch.id, { scope: "run" });
    expect(result.rollbackOf).toBe(batch.id);
    const byId = new Map(store.entries().map((entry) => [entry.id, entry]));
    expect([...byId.keys()].sort()).toEqual(["harness_gone", "harness_keep"]);
    expect(byId.get("harness_keep")?.content).toBe("v1");
    expect(byId.get("harness_gone")?.content).toBe("v1");
  });

  it("unknown id throws", () => {
    const store = new HarnessEntryStore(root);
    expect(() => store.rollback("refinement_nope", { scope: "run" })).toThrow(/unknown refinement/);
  });

  it("rollback is itself recorded", () => {
    const store = new HarnessEntryStore(root);
    const batch = store.apply([createEdit()], { scope: "run" });
    store.rollback(batch.id, { scope: "run" });
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
  it("rollback respects scope guardrail", () => {
    const store = new HarnessEntryStore(root);
    const batch = store.apply([createEdit({ id: "harness_g", title: "g", content: "v1" })], {
      scope: "global",
    });
    expect(() => store.rollback(batch.id, { scope: "run" })).toThrow(/scope_readonly/);
    // The rejected rollback mutated nothing: entry intact, no rollback recorded.
    expect(store.entries()[0].content).toBe("v1");
    expect(store.loadHistory()).toHaveLength(1);
    const undone = store.rollback(batch.id, { scope: "global" });
    expect(undone.rollbackOf).toBe(batch.id);
    expect(store.entries()).toHaveLength(0);
  });

  it("rollback delete path cannot remove a broader current occupant", () => {
    // Id reuse across scopes must not let a narrow rollback delete broad state.
    const store = new HarnessEntryStore(root);
    const created = store.apply([createEdit({ id: "harness_x", content: "run-v" })], {
      scope: "run",
    });
    store.apply([createEdit({ action: "delete", id: "harness_x" })], { scope: "run" });
    store.apply([createEdit({ id: "harness_x", content: "global-v" })], { scope: "global" });
    const result = store.rollback(created.id, { scope: "run" });
    expect(result.appliedEdits[0].applied).toBe(false);
    expect(result.appliedEdits[0].error).toBe("scope_readonly");
    const entry = store.entries()[0];
    expect(entry.scope).toBe("global");
    expect(entry.content).toBe("global-v");
  });

  it("rollback restore path cannot overwrite a broader current occupant", () => {
    const store = new HarnessEntryStore(root);
    store.apply([createEdit({ id: "harness_x", content: "v1" })], { scope: "run" });
    const updated = store.apply([createEdit({ action: "update", id: "harness_x", content: "v2" })], {
      scope: "run",
    });
    store.apply([createEdit({ action: "delete", id: "harness_x" })], { scope: "run" });
    store.apply([createEdit({ id: "harness_x", content: "global-v" })], { scope: "global" });
    const result = store.rollback(updated.id, { scope: "run" });
    expect(result.appliedEdits[0].applied).toBe(false);
    expect(result.appliedEdits[0].error).toBe("scope_readonly");
    const entry = store.entries()[0];
    expect(entry.scope).toBe("global");
    expect(entry.content).toBe("global-v");
  });

  it("broader caller can roll back a narrower refinement", () => {
    const store = new HarnessEntryStore(root);
    const batch = store.apply([createEdit({ id: "harness_r", title: "r", content: "v1" })], {
      scope: "run",
    });
    store.rollback(batch.id, { scope: "global" });
    expect(store.entries()).toHaveLength(0);
  });

  it("rollback rejects a missing scope at runtime", () => {
    const store = new HarnessEntryStore(root);
    const batch = store.apply([createEdit()], { scope: "run" });
    const opts: { scope?: "run" } = {};
    expect(() => store.rollback(batch.id, opts as { scope: "run" })).toThrow(/scope/);
  });

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
    const first = store.rollback(batch.id, { scope: "run" });
    expect(store.entries()[0].content).toBe("v1");
    store.rollback(first.id, { scope: "run" });
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
    store.rollback(batch.id, { scope: "run" });
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
    store.rollback(mid.id, { scope: "run" });
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

describe("skill reference (AC-899)", () => {
  const reference = {
    language: "python" as const,
    entrypoint: "priority",
    source: "def priority(v):\n    return sum(v)",
    callPattern: "priority(vector)",
    argumentsDescription: {},
  };

  it("apply carries reference and round-trips through the store", () => {
    const store = new HarnessEntryStore(root);
    store.apply(
      [
        {
          ...createEdit({
            kind: "procedure",
            id: "harness_skill",
            title: "Promoted skill: priority",
          }),
          reference,
        },
      ],
      { scope: "scenario_family" },
    );
    const entry = new HarnessEntryStore(root).entries({ kind: "procedure" })[0];
    expect(entry.reference?.entrypoint).toBe("priority");
    expect(entry.reference?.source).toContain("def priority");
  });

  it("update replaces reference when provided", () => {
    const store = new HarnessEntryStore(root);
    store.apply([{ ...createEdit({ kind: "procedure", id: "harness_s" }), reference }], {
      scope: "run",
    });
    store.apply(
      [
        {
          ...createEdit({ action: "update", kind: "procedure", id: "harness_s" }),
          reference: { ...reference, source: "def priority(v):\n    return max(v)" },
        },
      ],
      { scope: "run" },
    );
    expect(store.entries()[0].reference?.source).toContain("max(v)");
  });

  it("mis-kinded reference update is a per-edit error, not silent entry loss", () => {
    const store = new HarnessEntryStore(root);
    store.apply([createEdit({ kind: "fact", id: "harness_f" })], { scope: "run" });
    const batch = store.apply(
      [
        { ...createEdit({ action: "update", kind: "procedure", id: "harness_f" }), reference },
        createEdit({ kind: "fact", id: "harness_ok", title: "t2", content: "c2" }),
      ],
      { scope: "run" },
    );
    expect(batch.appliedEdits.map((item) => item.applied)).toEqual([false, true]);
    expect(batch.appliedEdits[0].error).toBe("reference_requires_procedure");
    const entries = new Map(new HarnessEntryStore(root).entries().map((e) => [e.id, e]));
    expect([...entries.keys()].sort()).toEqual(["harness_f", "harness_ok"]);
    expect(entries.get("harness_f")?.reference).toBeUndefined();
    expect(entries.get("harness_f")?.version).toBe(1);
  });

  it("non-procedure create carrying a reference is a per-edit error", () => {
    const store = new HarnessEntryStore(root);
    const batch = store.apply([{ ...createEdit({ kind: "fact", id: "harness_bad" }), reference }], {
      scope: "run",
    });
    expect(batch.appliedEdits[0].applied).toBe(false);
    expect(batch.appliedEdits[0].error).toBe("reference_requires_procedure");
    expect(new HarnessEntryStore(root).entries()).toEqual([]);
  });

  it("invalid stored reference drops the entry on load", () => {
    const store = new HarnessEntryStore(root);
    store.apply([{ ...createEdit({ kind: "procedure", id: "harness_good" }), reference }], {
      scope: "run",
    });
    const raw = JSON.parse(readFileSync(store.statePath, "utf8"));
    raw.entries.harness_bad = {
      ...raw.entries.harness_good,
      id: "harness_bad",
      reference: { language: "python", entrypoint: "", source: "" },
    };
    writeFileSync(store.statePath, JSON.stringify(raw), "utf8");
    expect(store.entries().map((entry) => entry.id)).toEqual(["harness_good"]);
  });
});


describe("polish (AC-908 parity with Python)", () => {
  it("clearExpectedOutcome clears on update and render drops the expected clause", () => {
    const store = new HarnessEntryStore(root);
    store.apply(
      [createEdit({ id: "harness_p", kind: "policy", expectedOutcome: "score rises" })],
      { scope: "run" },
    );
    expect(store.renderMarkdown()).toContain("(expected: score rises)");
    const result = store.apply(
      [createEdit({ action: "update", kind: "policy", id: "harness_p", clearExpectedOutcome: true })],
      { scope: "run" },
    );
    expect(result.appliedEdits[0].applied).toBe(true);
    const entry = store.entries()[0];
    expect(entry.expectedOutcome).toBe("");
    expect(entry.version).toBe(2);
    expect(store.renderMarkdown()).not.toContain("(expected:");
  });

  it("clearExpectedOutcome is rejected on create edits", () => {
    const store = new HarnessEntryStore(root);
    const result = store.apply([createEdit({ clearExpectedOutcome: true })], { scope: "run" });
    expect(result.appliedEdits[0].applied).toBe(false);
    expect(result.appliedEdits[0].error).toBe("clear_requires_update");
  });

  it("clearExpectedOutcome conflicts with a provided value", () => {
    const store = new HarnessEntryStore(root);
    store.apply([createEdit({ id: "harness_p", kind: "policy" })], { scope: "run" });
    const result = store.apply(
      [
        createEdit({
          action: "update",
          kind: "policy",
          id: "harness_p",
          expectedOutcome: "x",
          clearExpectedOutcome: true,
        }),
      ],
      { scope: "run" },
    );
    expect(result.appliedEdits[0].applied).toBe(false);
    expect(result.appliedEdits[0].error).toBe("clear_conflicts_with_expected_outcome");
  });

  it("update without the flag leaves expectedOutcome unchanged", () => {
    const store = new HarnessEntryStore(root);
    store.apply(
      [createEdit({ id: "harness_p", kind: "policy", expectedOutcome: "score rises" })],
      { scope: "run" },
    );
    store.apply([createEdit({ action: "update", kind: "policy", id: "harness_p", content: "c2" })], {
      scope: "run",
    });
    expect(store.entries()[0].expectedOutcome).toBe("score rises");
  });

  it("history lines without the clear flag still load", () => {
    const store = new HarnessEntryStore(root);
    store.apply([createEdit({ id: "harness_a" })], { scope: "run" });
    const raw = JSON.parse(readFileSync(join(root, "harness_refinements.jsonl"), "utf8").trim());
    for (const applied of raw.appliedEdits) delete applied.edit.clearExpectedOutcome;
    writeFileSync(join(root, "harness_refinements.jsonl"), JSON.stringify(raw) + "\n");
    const history = store.loadHistory();
    expect(history).toHaveLength(1);
    expect(history[0].appliedEdits[0].edit.clearExpectedOutcome ?? false).toBe(false);
  });

  it("renderMarkdown titles are newline-inert", () => {
    const store = new HarnessEntryStore(root);
    store.apply(
      [createEdit({ id: "harness_t", title: "ok\n- [harness_fake] injected", content: "body" })],
      { scope: "run" },
    );
    const text = store.renderMarkdown();
    expect(text).toContain("ok - [harness_fake] injected");
    for (const line of text.split("\n")) {
      expect(line.startsWith("- [harness_fake]")).toBe(false);
    }
  });

  it("renderMarkdown expectedOutcome and id are newline-inert", () => {
    const store = new HarnessEntryStore(root);
    store.apply(
      [
        createEdit({
          id: "harness_e",
          kind: "policy",
          expectedOutcome: "x)\n- [harness_fake] injected",
        }),
        createEdit({ id: "harness_i\n- [harness_fake2] injected", title: "t2", content: "body2" }),
      ],
      { scope: "run" },
    );
    const text = store.renderMarkdown();
    for (const line of text.split("\n")) {
      expect(line.startsWith("- [harness_fake]")).toBe(false);
      expect(line.startsWith("- [harness_fake2]")).toBe(false);
    }
  });

  it("renderMarkdown loads state exactly once", () => {
    let loads = 0;
    class CountingStore extends HarnessEntryStore {
      entries(opts: { kind?: HarnessEntryKind; scope?: HarnessScope } = {}) {
        loads += 1;
        return super.entries(opts);
      }
    }
    const store = new CountingStore(root);
    store.apply(
      [
        createEdit({ kind: "policy", title: "p" }),
        createEdit({ kind: "fact", title: "f" }),
        createEdit({ kind: "procedure", title: "pr" }),
      ],
      { scope: "run" },
    );
    loads = 0;
    store.renderMarkdown();
    expect(loads).toBe(1);
  });
});
