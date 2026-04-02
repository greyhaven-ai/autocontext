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
