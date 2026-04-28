import { describe, expect, it, beforeEach } from "vitest";
import { mkdtempSync } from "node:fs";
import { join } from "node:path";
import { tmpdir } from "node:os";
import { SessionStore } from "../src/session/store.js";
import { Session } from "../src/session/types.js";

describe("SessionStore", () => {
  let store: SessionStore;

  beforeEach(() => {
    const dir = mkdtempSync(join(tmpdir(), "sess-store-"));
    store = new SessionStore(join(dir, "sessions.db"));
  });

  it("save and load session", () => {
    const session = Session.create({ goal: "Build API" });
    store.save(session);
    const loaded = store.load(session.sessionId);
    expect(loaded).not.toBeNull();
    expect(loaded!.goal).toBe("Build API");
    expect(loaded!.sessionId).toBe(session.sessionId);
  });

  it("returns null for missing", () => {
    expect(store.load("nonexistent")).toBeNull();
  });

  it("update existing session", () => {
    const session = Session.create({ goal: "test" });
    store.save(session);
    session.submitTurn({ prompt: "do it", role: "researcher" });
    store.save(session);
    const loaded = store.load(session.sessionId);
    expect(loaded!.turns).toHaveLength(1);
  });

  it("round-trips branch lineage", () => {
    const session = Session.create({ goal: "explore" });
    const root = session.submitTurn({ prompt: "root", role: "competitor" });
    session.completeTurn(root.turnId, { response: "root response", tokensUsed: 10 });
    session.forkFromTurn(root.turnId, {
      branchId: "alt",
      label: "alternate path",
      summary: "early alternate",
    });
    const alt = session.submitTurn({ prompt: "alt", role: "analyst" });
    session.completeTurn(alt.turnId, { response: "alt response", tokensUsed: 20 });
    session.summarizeBranch("alt", "promising alternate");
    store.save(session);

    const loaded = store.load(session.sessionId);

    expect(loaded).not.toBeNull();
    expect(loaded!.activeBranchId).toBe("alt");
    expect(loaded!.activeTurnId).toBe(alt.turnId);
    expect(loaded!.branches.map((branch) => branch.branchId)).toEqual(["main", "alt"]);
    expect(loaded!.branches[1].parentTurnId).toBe(root.turnId);
    expect(loaded!.branches[1].summary).toBe("promising alternate");
    expect(loaded!.branchPath("alt").map((turn) => turn.turnId)).toEqual([root.turnId, alt.turnId]);
  });

  it("list sessions", () => {
    store.save(Session.create({ goal: "a" }));
    store.save(Session.create({ goal: "b" }));
    const all = store.list();
    expect(all).toHaveLength(2);
  });

  it("list by status", () => {
    const s1 = Session.create({ goal: "active" });
    const s2 = Session.create({ goal: "done" });
    s2.complete();
    store.save(s1);
    store.save(s2);
    expect(store.list("active")).toHaveLength(1);
    expect(store.list("completed")).toHaveLength(1);
  });

  it("delete session", () => {
    const session = Session.create({ goal: "delete me" });
    store.save(session);
    expect(store.delete(session.sessionId)).toBe(true);
    expect(store.load(session.sessionId)).toBeNull();
    expect(store.delete("nonexistent")).toBe(false);
  });
});
