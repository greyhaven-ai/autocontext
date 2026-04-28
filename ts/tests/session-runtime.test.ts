import { describe, expect, it } from "vitest";
import {
  Session,
  SessionEventType,
  SessionStatus,
  TurnOutcome,
} from "../src/session/types.js";

describe("Session domain model", () => {
  it("creates a session with active status", () => {
    const session = Session.create({ goal: "Implement REST API", metadata: { project: "acme" } });
    expect(session.sessionId).toBeTruthy();
    expect(session.status).toBe(SessionStatus.ACTIVE);
    expect(session.goal).toBe("Implement REST API");
    expect(session.metadata.project).toBe("acme");
    expect(session.turns).toHaveLength(0);
  });

  it("submits and completes a turn", () => {
    const session = Session.create({ goal: "test" });
    const turn = session.submitTurn({ prompt: "Write hello world", role: "competitor" });
    expect(turn.turnIndex).toBe(0);
    expect(turn.outcome).toBe(TurnOutcome.PENDING);

    session.completeTurn(turn.turnId, { response: "print('hello')", tokensUsed: 50 });
    expect(turn.outcome).toBe(TurnOutcome.COMPLETED);
    expect(turn.response).toBe("print('hello')");
    expect(turn.tokensUsed).toBe(50);
  });

  it("interrupts a turn (not mistaken for success)", () => {
    const session = Session.create({ goal: "test" });
    const turn = session.submitTurn({ prompt: "long task", role: "competitor" });
    session.interruptTurn(turn.turnId, "timeout");
    expect(turn.outcome).toBe(TurnOutcome.INTERRUPTED);
    expect(turn.succeeded).toBe(false);
  });

  it("transitions through lifecycle states", () => {
    const session = Session.create({ goal: "test" });
    expect(session.status).toBe(SessionStatus.ACTIVE);

    session.pause();
    expect(session.status).toBe(SessionStatus.PAUSED);

    session.resume();
    expect(session.status).toBe(SessionStatus.ACTIVE);

    session.complete("done");
    expect(session.status).toBe(SessionStatus.COMPLETED);
    expect(session.summary).toBe("done");
  });

  it("rejects turn submission when paused", () => {
    const session = Session.create({ goal: "test" });
    session.pause();
    expect(() => session.submitTurn({ prompt: "nope", role: "r" })).toThrow("not active");
  });

  it("does not allow terminal sessions to resume or accept new turns", () => {
    const session = Session.create({ goal: "test" });
    session.complete("done");

    expect(() => session.resume()).toThrow("status=completed");
    expect(() => session.submitTurn({ prompt: "again", role: "r" })).toThrow("not active");
  });

  it("tracks cumulative token usage", () => {
    const session = Session.create({ goal: "test" });
    const t1 = session.submitTurn({ prompt: "p1", role: "r1" });
    session.completeTurn(t1.turnId, { response: "r1", tokensUsed: 100 });
    const t2 = session.submitTurn({ prompt: "p2", role: "r2" });
    session.completeTurn(t2.turnId, { response: "r2", tokensUsed: 200 });
    expect(session.totalTokens).toBe(300);
    expect(session.turnCount).toBe(2);
  });

  it("emits session events", () => {
    const session = Session.create({ goal: "test" });
    expect(session.events.length).toBeGreaterThanOrEqual(1);
    expect(session.events[0].eventType).toBe(SessionEventType.SESSION_CREATED);

    const turn = session.submitTurn({ prompt: "p", role: "r" });
    session.completeTurn(turn.turnId, { response: "r", tokensUsed: 10 });
    const types = session.events.map((e) => e.eventType);
    expect(types).toContain(SessionEventType.TURN_SUBMITTED);
    expect(types).toContain(SessionEventType.TURN_COMPLETED);
  });
});

describe("Session branch lineage", () => {
  it("starts on the main branch", () => {
    const session = Session.create({ goal: "explore" });
    const turn = session.submitTurn({ prompt: "root", role: "competitor" });

    expect(session.activeBranchId).toBe("main");
    expect(session.activeTurnId).toBe(turn.turnId);
    expect(session.branches).toHaveLength(1);
    expect(session.branches[0].branchId).toBe("main");
    expect(session.branches[0].label).toBe("Main");
    expect(turn.branchId).toBe("main");
    expect(turn.parentTurnId).toBe("");
  });

  it("forks from a turn and switches to the new branch", () => {
    const session = Session.create({ goal: "explore" });
    const root = session.submitTurn({ prompt: "root", role: "competitor" });
    session.completeTurn(root.turnId, { response: "root response" });

    const branch = session.forkFromTurn(root.turnId, {
      branchId: "experimental",
      label: "try alternate",
    });
    const nextTurn = session.submitTurn({ prompt: "branch prompt", role: "competitor" });

    expect(branch.branchId).toBe("experimental");
    expect(branch.parentTurnId).toBe(root.turnId);
    expect(branch.label).toBe("try alternate");
    expect(session.activeBranchId).toBe("experimental");
    expect(nextTurn.branchId).toBe("experimental");
    expect(nextTurn.parentTurnId).toBe(root.turnId);
    expect(session.activeTurnId).toBe(nextTurn.turnId);

    const eventTypes = session.events.map((event) => event.eventType);
    expect(eventTypes).toContain(SessionEventType.BRANCH_CREATED);
    expect(eventTypes).toContain(SessionEventType.BRANCH_SWITCHED);
  });

  it("switches branches and parents the next turn to that branch leaf", () => {
    const session = Session.create({ goal: "explore" });
    const main = session.submitTurn({ prompt: "main", role: "competitor" });
    session.completeTurn(main.turnId, { response: "main response" });
    session.forkFromTurn(main.turnId, { branchId: "alt" });
    const alt = session.submitTurn({ prompt: "alt", role: "competitor" });
    session.completeTurn(alt.turnId, { response: "alt response" });

    session.switchBranch("main");
    const followup = session.submitTurn({ prompt: "main followup", role: "analyst" });

    expect(followup.branchId).toBe("main");
    expect(followup.parentTurnId).toBe(main.turnId);
  });

  it("returns only the selected branch lineage", () => {
    const session = Session.create({ goal: "explore" });
    const root = session.submitTurn({ prompt: "root", role: "competitor" });
    session.completeTurn(root.turnId, { response: "root response" });
    session.forkFromTurn(root.turnId, { branchId: "alt" });
    const alt = session.submitTurn({ prompt: "alt", role: "competitor" });
    session.completeTurn(alt.turnId, { response: "alt response" });

    expect(session.branchPath("alt").map((turn) => turn.turnId)).toEqual([root.turnId, alt.turnId]);
  });

  it("summarizes branches without rewriting turns", () => {
    const session = Session.create({ goal: "explore" });
    const root = session.submitTurn({ prompt: "root", role: "competitor" });

    session.summarizeBranch("main", "stable path");

    expect(session.branches[0].summary).toBe("stable path");
    expect(session.turns[0]).toBe(root);
    expect(session.events.map((event) => event.eventType)).toContain(SessionEventType.BRANCH_SUMMARIZED);
  });
});
