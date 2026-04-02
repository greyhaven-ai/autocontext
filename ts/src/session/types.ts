/**
 * Session runtime domain types (AC-507 TS parity).
 *
 * Port of Python autocontext.session.types — Session aggregate root
 * with Turn, SessionEvent, and explicit lifecycle management.
 */

import { randomUUID } from "node:crypto";

// ---- Enums ----

export const SessionStatus = {
  ACTIVE: "active",
  PAUSED: "paused",
  COMPLETED: "completed",
  FAILED: "failed",
  CANCELED: "canceled",
} as const;
export type SessionStatus = (typeof SessionStatus)[keyof typeof SessionStatus];

export const TurnOutcome = {
  PENDING: "pending",
  COMPLETED: "completed",
  INTERRUPTED: "interrupted",
  FAILED: "failed",
  BUDGET_EXHAUSTED: "budget_exhausted",
} as const;
export type TurnOutcome = (typeof TurnOutcome)[keyof typeof TurnOutcome];

export const SessionEventType = {
  SESSION_CREATED: "session_created",
  SESSION_PAUSED: "session_paused",
  SESSION_RESUMED: "session_resumed",
  SESSION_COMPLETED: "session_completed",
  SESSION_FAILED: "session_failed",
  SESSION_CANCELED: "session_canceled",
  TURN_SUBMITTED: "turn_submitted",
  TURN_COMPLETED: "turn_completed",
  TURN_INTERRUPTED: "turn_interrupted",
  TURN_FAILED: "turn_failed",
} as const;
export type SessionEventType = (typeof SessionEventType)[keyof typeof SessionEventType];

const TERMINAL_SESSION_STATUSES = new Set<SessionStatus>([
  SessionStatus.COMPLETED,
  SessionStatus.FAILED,
  SessionStatus.CANCELED,
]);

// ---- Value Objects ----

export interface SessionEvent {
  readonly eventId: string;
  readonly eventType: SessionEventType;
  readonly timestamp: string;
  readonly payload: Record<string, unknown>;
}

function createEvent(
  eventType: SessionEventType,
  payload: Record<string, unknown>,
): SessionEvent {
  return {
    eventId: randomUUID().slice(0, 12),
    eventType,
    timestamp: new Date().toISOString(),
    payload,
  };
}

// ---- Turn Entity ----

export class Turn {
  readonly turnId: string;
  readonly turnIndex: number;
  readonly prompt: string;
  readonly role: string;
  response: string = "";
  outcome: TurnOutcome = TurnOutcome.PENDING;
  error: string = "";
  tokensUsed: number = 0;
  readonly startedAt: string;
  completedAt: string = "";

  constructor(opts: { turnIndex: number; prompt: string; role: string }) {
    this.turnId = randomUUID().slice(0, 12);
    this.turnIndex = opts.turnIndex;
    this.prompt = opts.prompt;
    this.role = opts.role;
    this.startedAt = new Date().toISOString();
  }

  get succeeded(): boolean {
    return this.outcome === TurnOutcome.COMPLETED;
  }
}

// ---- Session Aggregate Root ----

export class Session {
  readonly sessionId: string;
  readonly goal: string;
  status: SessionStatus = SessionStatus.ACTIVE;
  summary: string = "";
  readonly metadata: Record<string, unknown>;
  readonly turns: Turn[] = [];
  readonly events: SessionEvent[] = [];
  readonly createdAt: string;
  updatedAt: string = "";

  private constructor(opts: { goal: string; metadata?: Record<string, unknown> }) {
    this.sessionId = randomUUID().slice(0, 16);
    this.goal = opts.goal;
    this.metadata = opts.metadata ?? {};
    this.createdAt = new Date().toISOString();
  }

  static create(opts: { goal: string; metadata?: Record<string, unknown> }): Session {
    const session = new Session(opts);
    session.emit(SessionEventType.SESSION_CREATED, { goal: opts.goal });
    return session;
  }

  // -- Turn management --

  submitTurn(opts: { prompt: string; role: string }): Turn {
    if (this.status !== SessionStatus.ACTIVE) {
      throw new Error(`Cannot submit turn: session is not active (status=${this.status})`);
    }
    const turn = new Turn({ turnIndex: this.turns.length, ...opts });
    this.turns.push(turn);
    this.emit(SessionEventType.TURN_SUBMITTED, { turnId: turn.turnId, role: opts.role });
    return turn;
  }

  completeTurn(turnId: string, opts: { response: string; tokensUsed?: number }): void {
    const turn = this.getTurn(turnId);
    turn.outcome = TurnOutcome.COMPLETED;
    turn.response = opts.response;
    turn.tokensUsed = opts.tokensUsed ?? 0;
    turn.completedAt = new Date().toISOString();
    this.touch();
    this.emit(SessionEventType.TURN_COMPLETED, { turnId, tokensUsed: turn.tokensUsed });
  }

  interruptTurn(turnId: string, reason: string = ""): void {
    const turn = this.getTurn(turnId);
    turn.outcome = TurnOutcome.INTERRUPTED;
    turn.error = reason;
    turn.completedAt = new Date().toISOString();
    this.touch();
    this.emit(SessionEventType.TURN_INTERRUPTED, { turnId, reason });
  }

  failTurn(turnId: string, error: string = ""): void {
    const turn = this.getTurn(turnId);
    turn.outcome = TurnOutcome.FAILED;
    turn.error = error;
    turn.completedAt = new Date().toISOString();
    this.touch();
    this.emit(SessionEventType.TURN_FAILED, { turnId, error });
  }

  // -- Lifecycle --

  pause(): void {
    this.requireStatus(SessionStatus.ACTIVE, "pause");
    this.status = SessionStatus.PAUSED;
    this.touch();
    this.emit(SessionEventType.SESSION_PAUSED, {});
  }

  resume(): void {
    this.requireStatus(SessionStatus.PAUSED, "resume");
    this.status = SessionStatus.ACTIVE;
    this.touch();
    this.emit(SessionEventType.SESSION_RESUMED, {});
  }

  complete(summary: string = ""): void {
    this.requireNotTerminal("complete");
    this.status = SessionStatus.COMPLETED;
    this.summary = summary;
    this.touch();
    this.emit(SessionEventType.SESSION_COMPLETED, { summary });
  }

  fail(error: string = ""): void {
    this.requireNotTerminal("fail");
    this.status = SessionStatus.FAILED;
    this.touch();
    this.emit(SessionEventType.SESSION_FAILED, { error });
  }

  cancel(): void {
    this.requireNotTerminal("cancel");
    this.status = SessionStatus.CANCELED;
    this.touch();
    this.emit(SessionEventType.SESSION_CANCELED, {});
  }

  // -- Queries --

  get totalTokens(): number {
    return this.turns.reduce((sum, t) => sum + t.tokensUsed, 0);
  }

  get turnCount(): number {
    return this.turns.length;
  }

  // -- Internal --

  private getTurn(turnId: string): Turn {
    const turn = this.turns.find((t) => t.turnId === turnId);
    if (!turn) throw new Error(`Turn ${turnId} not found in session ${this.sessionId}`);
    return turn;
  }

  private requireStatus(expected: SessionStatus, action: string): void {
    if (this.status !== expected) {
      throw new Error(`Cannot ${action} session from status=${this.status}`);
    }
  }

  private requireNotTerminal(action: string): void {
    if (TERMINAL_SESSION_STATUSES.has(this.status)) {
      throw new Error(`Cannot ${action} session from terminal status=${this.status}`);
    }
  }

  private touch(): void {
    this.updatedAt = new Date().toISOString();
  }

  private emit(eventType: SessionEventType, payload: Record<string, unknown>): void {
    this.events.push(createEvent(eventType, { sessionId: this.sessionId, ...payload }));
  }
}
