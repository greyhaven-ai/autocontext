/**
 * Typed harness entries with scoped CRUD edits and rollback (AC-898).
 * Mirrors Python's autocontext/knowledge/harness_entries.py.
 *
 * Lessons, policies, procedures, and delegation specs persisted as typed,
 * scoped, versioned entries instead of accumulated prose. Every mutation is
 * a CRUD edit recorded to an append-only refinement history with
 * before/after snapshots, so any refinement can be rolled back. Adds
 * outcome marking so verifier-scored runs can confirm or refute an entry's
 * expected outcome.
 *
 * The store assumes a single writer per root directory: writes are atomic
 * but load-modify-save, so concurrent writers are last-writer-wins. State
 * files are per-language (Python writes snake_case fields) and are not
 * interchangeable across the two runtimes.
 */

import {
  appendFileSync,
  existsSync,
  mkdirSync,
  readFileSync,
  renameSync,
  unlinkSync,
  writeFileSync,
} from "node:fs";
import { join } from "node:path";
import { randomUUID } from "node:crypto";

export type HarnessEntryKind = "policy" | "fact" | "procedure" | "delegation";
export type HarnessScope = "run" | "scenario_family" | "global";
export type HarnessOutcome = "pending" | "confirmed" | "refuted";
export type HarnessEditAction = "create" | "update" | "delete";

export const SCOPE_ORDER: Record<HarnessScope, number> = {
  run: 0,
  scenario_family: 1,
  global: 2,
};

export const STATE_FILE_NAME = "harness_state.json";
export const HISTORY_FILE_NAME = "harness_refinements.jsonl";

export const KIND_HEADINGS: Record<HarnessEntryKind, string> = {
  policy: "Policies",
  fact: "Facts",
  procedure: "Procedures",
  delegation: "Delegations",
};

const KINDS: HarnessEntryKind[] = ["policy", "fact", "procedure", "delegation"];
const SCOPES: HarnessScope[] = ["run", "scenario_family", "global"];
const OUTCOMES: HarnessOutcome[] = ["pending", "confirmed", "refuted"];

/** One typed, scoped, versioned harness entry. */
export interface HarnessEntry {
  id: string;
  kind: HarnessEntryKind;
  scope: HarnessScope;
  title: string;
  content: string;
  expectedOutcome: string;
  outcome: HarnessOutcome;
  outcomeEvidence: string;
  source: string;
  createdAt: string;
  updatedAt: string;
  version: number;
}

/** A single create/update/delete request against the store. */
export interface HarnessEdit {
  action: HarnessEditAction;
  kind: HarnessEntryKind;
  id: string;
  title: string;
  content: string;
  expectedOutcome: string;
  reason: string;
}

/** An edit plus what actually happened when it was applied. */
export interface AppliedHarnessEdit {
  edit: HarnessEdit;
  entryId: string;
  applied: boolean;
  error: string;
  before?: HarnessEntry;
  after?: HarnessEntry;
}

/** One recorded refinement: a batch of applied edits at one scope. */
export interface HarnessRefinement {
  id: string;
  scope: HarnessScope;
  summary: string;
  appliedEdits: AppliedHarnessEdit[];
  rollbackOf: string;
  createdAt: string;
}

export interface HarnessEntryStoreOpts {
  nowIso?: () => string;
}

export interface HarnessApplyOpts {
  scope: HarnessScope;
  summary?: string;
  source?: string;
  rollbackOf?: string;
}

function shortId(prefix: string): string {
  return `${prefix}_${randomUUID().replace(/-/g, "").slice(0, 8)}`;
}

function isRecord(value: unknown): value is Record<string, unknown> {
  return typeof value === "object" && value !== null && !Array.isArray(value);
}

const ACTIONS: HarnessEditAction[] = ["create", "update", "delete"];

function isKind(value: unknown): value is HarnessEntryKind {
  return KINDS.some((kind) => kind === value);
}

function isScope(value: unknown): value is HarnessScope {
  return SCOPES.some((scope) => scope === value);
}

function isOutcome(value: unknown): value is HarnessOutcome {
  return OUTCOMES.some((outcome) => outcome === value);
}

function isAction(value: unknown): value is HarnessEditAction {
  return ACTIONS.some((action) => action === value);
}

function normalizeEdit(raw: unknown): HarnessEdit | undefined {
  if (!isRecord(raw)) return undefined;
  const { action, kind } = raw;
  if (!isAction(action) || !isKind(kind)) return undefined;
  return {
    action,
    kind,
    id: typeof raw.id === "string" ? raw.id : "",
    title: typeof raw.title === "string" ? raw.title : "",
    content: typeof raw.content === "string" ? raw.content : "",
    expectedOutcome: typeof raw.expectedOutcome === "string" ? raw.expectedOutcome : "",
    reason: typeof raw.reason === "string" ? raw.reason : "",
  };
}

function normalizeEntry(raw: unknown): HarnessEntry | undefined {
  if (!isRecord(raw)) return undefined;
  const { kind, scope } = raw;
  const outcome = raw.outcome ?? "pending";
  if (typeof raw.id !== "string" || raw.id === "") return undefined;
  if (!isKind(kind) || !isScope(scope) || !isOutcome(outcome)) return undefined;
  if (typeof raw.title !== "string" || typeof raw.content !== "string") return undefined;
  return {
    id: raw.id,
    kind,
    scope,
    title: raw.title,
    content: raw.content,
    expectedOutcome: typeof raw.expectedOutcome === "string" ? raw.expectedOutcome : "",
    outcome,
    outcomeEvidence: typeof raw.outcomeEvidence === "string" ? raw.outcomeEvidence : "",
    source: typeof raw.source === "string" ? raw.source : "",
    createdAt: typeof raw.createdAt === "string" ? raw.createdAt : "",
    updatedAt: typeof raw.updatedAt === "string" ? raw.updatedAt : "",
    version: typeof raw.version === "number" ? raw.version : 1,
  };
}

function normalizeRefinement(raw: unknown): HarnessRefinement | undefined {
  if (!isRecord(raw)) return undefined;
  const { scope } = raw;
  if (typeof raw.id !== "string" || raw.id === "" || !isScope(scope)) return undefined;
  if (!Array.isArray(raw.appliedEdits)) return undefined;
  const appliedEdits: AppliedHarnessEdit[] = [];
  for (const item of raw.appliedEdits) {
    if (!isRecord(item) || typeof item.entryId !== "string" || typeof item.applied !== "boolean")
      return undefined;
    const edit = normalizeEdit(item.edit);
    if (!edit) return undefined;
    let before: HarnessEntry | undefined;
    let after: HarnessEntry | undefined;
    if (item.before !== undefined && item.before !== null) {
      before = normalizeEntry(item.before);
      if (!before) return undefined;
    }
    if (item.after !== undefined && item.after !== null) {
      after = normalizeEntry(item.after);
      if (!after) return undefined;
    }
    appliedEdits.push({
      edit,
      entryId: item.entryId,
      applied: item.applied,
      error: typeof item.error === "string" ? item.error : "",
      before,
      after,
    });
  }
  return {
    id: raw.id,
    scope,
    summary: typeof raw.summary === "string" ? raw.summary : "",
    appliedEdits,
    rollbackOf: typeof raw.rollbackOf === "string" ? raw.rollbackOf : "",
    createdAt: typeof raw.createdAt === "string" ? raw.createdAt : "",
  };
}

/** JSON-state + JSONL-history store for typed harness entries. */
export class HarnessEntryStore {
  readonly root: string;
  private readonly nowIso: () => string;

  constructor(root: string, opts: HarnessEntryStoreOpts = {}) {
    this.root = root;
    this.nowIso = opts.nowIso ?? (() => new Date().toISOString());
  }

  get statePath(): string {
    return join(this.root, STATE_FILE_NAME);
  }

  get historyPath(): string {
    return join(this.root, HISTORY_FILE_NAME);
  }

  /**
   * Apply a batch of edits at one scope; record and return the refinement.
   *
   * An empty batch is a no-op: nothing is persisted and the returned
   * refinement is not recorded in history.
   */
  apply(edits: HarnessEdit[], opts: HarnessApplyOpts): HarnessRefinement {
    const state = this.loadState();
    const applied = edits.map((edit) => this.applyEdit(state, edit, opts.scope, opts.source ?? ""));
    const refinement: HarnessRefinement = {
      id: shortId("refinement"),
      scope: opts.scope,
      summary: opts.summary ?? "",
      appliedEdits: applied,
      rollbackOf: opts.rollbackOf ?? "",
      createdAt: this.nowIso(),
    };
    if (edits.length === 0) return refinement;
    this.appendHistory(refinement);
    this.saveState(state);
    return refinement;
  }

  entries(filter: { kind?: HarnessEntryKind; scope?: HarnessScope } = {}): HarnessEntry[] {
    let out = [...this.loadState().values()];
    if (filter.kind !== undefined) out = out.filter((entry) => entry.kind === filter.kind);
    if (filter.scope !== undefined) out = out.filter((entry) => entry.scope === filter.scope);
    return out.sort((a, b) =>
      a.createdAt === b.createdAt
        ? a.id.localeCompare(b.id)
        : a.createdAt.localeCompare(b.createdAt),
    );
  }

  /** Refinements in append order; malformed lines are skipped so one torn append cannot break rollback. */
  loadHistory(): HarnessRefinement[] {
    if (!existsSync(this.historyPath)) return [];
    const out: HarnessRefinement[] = [];
    for (const line of readFileSync(this.historyPath, "utf8").split("\n")) {
      const stripped = line.trim();
      if (!stripped) continue;
      try {
        const refinement = normalizeRefinement(JSON.parse(stripped));
        if (refinement) out.push(refinement);
      } catch {
        continue;
      }
    }
    return out;
  }

  /**
   * Invert a recorded refinement by restoring its before-snapshots.
   *
   * One-step semantics: snapshots are restored blindly, so edits made to
   * the same entries by later refinements are overwritten (lost update).
   * Outcome marks are measurements, not refinement effects, so a current
   * non-pending outcome survives the restore.
   */
  rollback(refinementId: string): HarnessRefinement {
    const target = this.loadHistory().find((r) => r.id === refinementId);
    if (!target) {
      throw new Error(`unknown refinement: ${refinementId}`);
    }
    const state = this.loadState();
    const applied: AppliedHarnessEdit[] = [];
    const reason = `rollback of ${refinementId}`;
    for (const item of [...target.appliedEdits].reverse()) {
      if (!item.applied) continue;
      if (item.before === undefined || item.before === null) {
        const removed = state.get(item.entryId);
        state.delete(item.entryId);
        applied.push({
          edit: {
            action: "delete",
            kind: item.edit.kind,
            id: item.entryId,
            title: "",
            content: "",
            expectedOutcome: "",
            reason,
          },
          entryId: item.entryId,
          applied: removed !== undefined,
          error: removed !== undefined ? "" : "not_found",
          before: removed,
        });
      } else {
        const restored: HarnessEntry = { ...item.before };
        const previous = state.get(item.entryId);
        if (previous !== undefined && previous.outcome !== "pending") {
          restored.outcome = previous.outcome;
          restored.outcomeEvidence = previous.outcomeEvidence;
        }
        state.set(item.entryId, restored);
        const action: HarnessEditAction = previous !== undefined ? "update" : "create";
        applied.push({
          edit: {
            action,
            kind: restored.kind,
            id: item.entryId,
            title: "",
            content: "",
            expectedOutcome: "",
            reason,
          },
          entryId: item.entryId,
          applied: true,
          error: "",
          before: previous,
          after: restored,
        });
      }
    }
    const refinement: HarnessRefinement = {
      id: shortId("refinement"),
      scope: target.scope,
      summary: reason,
      appliedEdits: applied,
      rollbackOf: refinementId,
      createdAt: this.nowIso(),
    };
    this.appendHistory(refinement);
    this.saveState(state);
    return refinement;
  }

  /**
   * Record a measured outcome for an entry's expectedOutcome.
   *
   * Outcome marks are measurements, not refinements: they update state in
   * place and are not recorded to the refinement history. The same scope
   * guardrail as `apply` holds: a narrower-scope caller cannot mark a
   * broader-scope entry.
   */
  markOutcome(
    entryId: string,
    outcome: "confirmed" | "refuted",
    opts: { scope: HarnessScope; evidence?: string },
  ): HarnessEntry {
    const state = this.loadState();
    const existing = state.get(entryId);
    if (!existing) {
      throw new Error(`unknown harness entry: ${entryId}`);
    }
    if (SCOPE_ORDER[existing.scope] > SCOPE_ORDER[opts.scope]) {
      throw new Error(`scope_readonly: ${entryId} is ${existing.scope}-scoped`);
    }
    const updated: HarnessEntry = { ...existing, outcome };
    if (opts.evidence) updated.outcomeEvidence = opts.evidence;
    updated.updatedAt = this.nowIso();
    updated.version = existing.version + 1;
    state.set(entryId, updated);
    this.saveState(state);
    return updated;
  }

  /** Markdown for prompt injection: grouped by kind, refuted entries excluded. */
  renderMarkdown(opts: { kinds?: HarnessEntryKind[] } = {}): string {
    const selected = opts.kinds ?? KINDS;
    const sections: string[] = [];
    for (const kind of selected) {
      const visible = this.entries({ kind }).filter((entry) => entry.outcome !== "refuted");
      if (visible.length === 0) continue;
      const lines = [`### ${KIND_HEADINGS[kind]}`];
      for (const entry of visible) {
        const content = entry.content.replace(/\n/g, "\n  ");
        let line = `- [${entry.id}] ${entry.title}: ${content}`;
        if (entry.expectedOutcome) line += ` (expected: ${entry.expectedOutcome})`;
        lines.push(line);
      }
      sections.push(lines.join("\n"));
    }
    if (sections.length === 0) return "";
    return "## Harness Entries\n\n" + sections.join("\n\n") + "\n";
  }

  private applyEdit(
    state: Map<string, HarnessEntry>,
    edit: HarnessEdit,
    scope: HarnessScope,
    source: string,
  ): AppliedHarnessEdit {
    if (edit.action === "create") {
      const entryId = edit.id || shortId("harness");
      if (state.has(entryId)) {
        return { edit, entryId, applied: false, error: "duplicate_id" };
      }
      const now = this.nowIso();
      const entry: HarnessEntry = {
        id: entryId,
        kind: edit.kind,
        scope,
        title: edit.title,
        content: edit.content,
        expectedOutcome: edit.expectedOutcome,
        outcome: "pending",
        outcomeEvidence: "",
        source,
        createdAt: now,
        updatedAt: now,
        version: 1,
      };
      state.set(entryId, entry);
      return { edit, entryId, applied: true, error: "", after: entry };
    }

    const existing = state.get(edit.id);
    if (!existing) {
      return { edit, entryId: edit.id, applied: false, error: "not_found" };
    }
    if (SCOPE_ORDER[existing.scope] > SCOPE_ORDER[scope]) {
      return { edit, entryId: edit.id, applied: false, error: "scope_readonly" };
    }
    const before: HarnessEntry = { ...existing };
    if (edit.action === "delete") {
      state.delete(edit.id);
      return { edit, entryId: edit.id, applied: true, error: "", before };
    }
    const updated: HarnessEntry = { ...existing };
    if (edit.title) updated.title = edit.title;
    if (edit.content) updated.content = edit.content;
    if (edit.expectedOutcome) updated.expectedOutcome = edit.expectedOutcome;
    updated.updatedAt = this.nowIso();
    updated.version = existing.version + 1;
    state.set(edit.id, updated);
    return { edit, entryId: edit.id, applied: true, error: "", before, after: updated };
  }

  /** Corrupt or unreadable state degrades to empty; the next save rewrites it cleanly. */
  private loadState(): Map<string, HarnessEntry> {
    const entries = new Map<string, HarnessEntry>();
    if (!existsSync(this.statePath)) return entries;
    let raw: unknown;
    try {
      raw = JSON.parse(readFileSync(this.statePath, "utf8"));
    } catch {
      return entries;
    }
    if (!isRecord(raw) || !isRecord(raw.entries)) return entries;
    for (const value of Object.values(raw.entries)) {
      const entry = normalizeEntry(value);
      if (entry) entries.set(entry.id, entry);
    }
    return entries;
  }

  private saveState(entries: Map<string, HarnessEntry>): void {
    mkdirSync(this.root, { recursive: true });
    const sorted = [...entries.entries()].sort(([a], [b]) => a.localeCompare(b));
    const payload = { schema: 1, entries: Object.fromEntries(sorted) };
    const tmp = `${this.statePath}.${process.pid}.${randomUUID()}.tmp`;
    try {
      writeFileSync(tmp, JSON.stringify(payload, null, 2) + "\n", "utf8");
      renameSync(tmp, this.statePath);
    } finally {
      if (existsSync(tmp)) unlinkSync(tmp);
    }
  }

  private appendHistory(refinement: HarnessRefinement): void {
    mkdirSync(this.root, { recursive: true });
    appendFileSync(this.historyPath, JSON.stringify(refinement) + "\n", "utf8");
  }
}
