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
  BRANCH_CREATED: "branch_created",
  BRANCH_SWITCHED: "branch_switched",
  BRANCH_SUMMARIZED: "branch_summarized",
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

function isRecord(value: unknown): value is Record<string, unknown> {
  return typeof value === "object" && value !== null && !Array.isArray(value);
}

function readRecord(data: Record<string, unknown>, key: string): Record<string, unknown> | undefined {
  const value = data[key];
  return isRecord(value) ? value : undefined;
}

function readRecordArray(data: Record<string, unknown>, key: string): Record<string, unknown>[] {
  const value = data[key];
  return Array.isArray(value) ? value.filter(isRecord) : [];
}

function readString(data: Record<string, unknown>, key: string, fallback = ""): string {
  const value = data[key];
  return typeof value === "string" ? value : fallback;
}

function readNumber(data: Record<string, unknown>, key: string, fallback = 0): number {
  const value = data[key];
  return typeof value === "number" ? value : fallback;
}

function readTurnOutcome(data: Record<string, unknown>, key: string): TurnOutcome {
  switch (data[key]) {
    case TurnOutcome.COMPLETED:
      return TurnOutcome.COMPLETED;
    case TurnOutcome.INTERRUPTED:
      return TurnOutcome.INTERRUPTED;
    case TurnOutcome.FAILED:
      return TurnOutcome.FAILED;
    case TurnOutcome.BUDGET_EXHAUSTED:
      return TurnOutcome.BUDGET_EXHAUSTED;
    case TurnOutcome.PENDING:
    default:
      return TurnOutcome.PENDING;
  }
}

function readSessionStatus(data: Record<string, unknown>, key: string): SessionStatus {
  switch (data[key]) {
    case SessionStatus.PAUSED:
      return SessionStatus.PAUSED;
    case SessionStatus.COMPLETED:
      return SessionStatus.COMPLETED;
    case SessionStatus.FAILED:
      return SessionStatus.FAILED;
    case SessionStatus.CANCELED:
      return SessionStatus.CANCELED;
    case SessionStatus.ACTIVE:
    default:
      return SessionStatus.ACTIVE;
  }
}

function readSessionEventType(data: Record<string, unknown>, key: string): SessionEventType | undefined {
  switch (data[key]) {
    case SessionEventType.SESSION_CREATED:
      return SessionEventType.SESSION_CREATED;
    case SessionEventType.SESSION_PAUSED:
      return SessionEventType.SESSION_PAUSED;
    case SessionEventType.SESSION_RESUMED:
      return SessionEventType.SESSION_RESUMED;
    case SessionEventType.SESSION_COMPLETED:
      return SessionEventType.SESSION_COMPLETED;
    case SessionEventType.SESSION_FAILED:
      return SessionEventType.SESSION_FAILED;
    case SessionEventType.SESSION_CANCELED:
      return SessionEventType.SESSION_CANCELED;
    case SessionEventType.TURN_SUBMITTED:
      return SessionEventType.TURN_SUBMITTED;
    case SessionEventType.TURN_COMPLETED:
      return SessionEventType.TURN_COMPLETED;
    case SessionEventType.TURN_INTERRUPTED:
      return SessionEventType.TURN_INTERRUPTED;
    case SessionEventType.TURN_FAILED:
      return SessionEventType.TURN_FAILED;
    case SessionEventType.BRANCH_CREATED:
      return SessionEventType.BRANCH_CREATED;
    case SessionEventType.BRANCH_SWITCHED:
      return SessionEventType.BRANCH_SWITCHED;
    case SessionEventType.BRANCH_SUMMARIZED:
      return SessionEventType.BRANCH_SUMMARIZED;
    default:
      return undefined;
  }
}

function readSessionEvent(data: Record<string, unknown>): SessionEvent | undefined {
  const eventId = readString(data, "eventId");
  const eventType = readSessionEventType(data, "eventType");
  const timestamp = readString(data, "timestamp");
  const payload = readRecord(data, "payload");
  if (!eventId || !eventType || !timestamp || !payload) return undefined;
  return { eventId, eventType, timestamp, payload };
}

// ---- Turn Entity ----

export class Turn {
  readonly turnId: string;
  readonly turnIndex: number;
  readonly prompt: string;
  readonly role: string;
  readonly parentTurnId: string;
  readonly branchId: string;
  response: string = "";
  outcome: TurnOutcome = TurnOutcome.PENDING;
  error: string = "";
  tokensUsed: number = 0;
  readonly startedAt: string;
  completedAt: string = "";

  constructor(opts: {
    turnIndex: number;
    prompt: string;
    role: string;
    parentTurnId?: string;
    branchId?: string;
  }) {
    this.turnId = randomUUID().slice(0, 12);
    this.turnIndex = opts.turnIndex;
    this.prompt = opts.prompt;
    this.role = opts.role;
    this.parentTurnId = opts.parentTurnId ?? "";
    this.branchId = opts.branchId ?? "main";
    this.startedAt = new Date().toISOString();
  }

  get succeeded(): boolean {
    return this.outcome === TurnOutcome.COMPLETED;
  }

  toJSON(): Record<string, unknown> {
    return {
      turnId: this.turnId, turnIndex: this.turnIndex, prompt: this.prompt,
      role: this.role, parentTurnId: this.parentTurnId, branchId: this.branchId,
      response: this.response, outcome: this.outcome,
      error: this.error, tokensUsed: this.tokensUsed,
      startedAt: this.startedAt, completedAt: this.completedAt,
    };
  }

  static fromJSON(data: Record<string, unknown>, opts: { parentTurnId?: string; branchId?: string } = {}): Turn {
    const t = new Turn({
      turnIndex: readNumber(data, "turnIndex"),
      prompt: readString(data, "prompt"),
      role: readString(data, "role"),
      parentTurnId: readString(data, "parentTurnId", opts.parentTurnId ?? ""),
      branchId: readString(data, "branchId", opts.branchId ?? "main"),
    });
    Object.assign(t, {
      turnId: readString(data, "turnId", t.turnId),
      response: readString(data, "response"),
      outcome: readTurnOutcome(data, "outcome"),
      error: readString(data, "error"),
      tokensUsed: readNumber(data, "tokensUsed"),
      startedAt: readString(data, "startedAt", t.startedAt),
      completedAt: readString(data, "completedAt"),
    });
    return t;
  }
}

// ---- Branch Entity ----

export class Branch {
  readonly branchId: string;
  readonly parentTurnId: string;
  readonly label: string;
  summary: string;
  readonly createdAt: string;

  constructor(opts: {
    branchId: string;
    parentTurnId?: string;
    label?: string;
    summary?: string;
    createdAt?: string;
  }) {
    this.branchId = opts.branchId;
    this.parentTurnId = opts.parentTurnId ?? "";
    this.label = opts.label ?? "";
    this.summary = opts.summary ?? "";
    this.createdAt = opts.createdAt ?? new Date().toISOString();
  }

  toJSON(): Record<string, unknown> {
    return {
      branchId: this.branchId,
      parentTurnId: this.parentTurnId,
      label: this.label,
      summary: this.summary,
      createdAt: this.createdAt,
    };
  }

  static fromJSON(data: Record<string, unknown>): Branch {
    return new Branch({
      branchId: readString(data, "branchId"),
      parentTurnId: readString(data, "parentTurnId"),
      label: readString(data, "label"),
      summary: readString(data, "summary"),
      createdAt: readString(data, "createdAt", new Date().toISOString()),
    });
  }
}

// ---- Session Aggregate Root ----

export class Session {
  readonly sessionId: string;
  readonly goal: string;
  status: SessionStatus = SessionStatus.ACTIVE;
  summary: string = "";
  readonly metadata: Record<string, unknown>;
  activeBranchId: string = "main";
  activeTurnId: string = "";
  readonly branches: Branch[] = [new Branch({ branchId: "main", label: "Main" })];
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
    const turn = new Turn({
      turnIndex: this.turns.length,
      ...opts,
      parentTurnId: this.activeTurnId,
      branchId: this.activeBranchId,
    });
    this.turns.push(turn);
    this.activeTurnId = turn.turnId;
    this.touch();
    this.emit(SessionEventType.TURN_SUBMITTED, {
      turnId: turn.turnId,
      role: opts.role,
      branchId: turn.branchId,
      parentTurnId: turn.parentTurnId,
    });
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

  // -- Branch management --

  forkFromTurn(turnId: string, opts: { branchId?: string; label?: string; summary?: string } = {}): Branch {
    const parent = this.getTurn(turnId);
    const branchId = opts.branchId ?? randomUUID().slice(0, 8);
    if (this.branches.some((branch) => branch.branchId === branchId)) {
      throw new Error(`Branch ${branchId} already exists`);
    }

    const branch = new Branch({
      branchId,
      parentTurnId: parent.turnId,
      label: opts.label ?? "",
      summary: opts.summary ?? "",
    });
    this.branches.push(branch);
    this.touch();
    this.emit(SessionEventType.BRANCH_CREATED, {
      branchId: branch.branchId,
      parentTurnId: branch.parentTurnId,
      label: branch.label,
    });
    this.switchBranch(branch.branchId);
    return branch;
  }

  switchBranch(branchId: string): void {
    const branch = this.getBranch(branchId);
    this.activeBranchId = branch.branchId;
    this.activeTurnId = this.branchLeafTurnId(branch.branchId);
    this.touch();
    this.emit(SessionEventType.BRANCH_SWITCHED, {
      branchId: branch.branchId,
      activeTurnId: this.activeTurnId,
    });
  }

  summarizeBranch(branchId: string, summary: string): void {
    const branch = this.getBranch(branchId);
    branch.summary = summary;
    this.touch();
    this.emit(SessionEventType.BRANCH_SUMMARIZED, { branchId, summary });
  }

  // -- Queries --

  get totalTokens(): number {
    return this.turns.reduce((sum, t) => sum + t.tokensUsed, 0);
  }

  get turnCount(): number {
    return this.turns.length;
  }

  branchPath(branchId?: string): Turn[] {
    const resolvedBranchId = branchId ?? this.activeBranchId;
    this.getBranch(resolvedBranchId);
    const byId = new Map(this.turns.map((turn) => [turn.turnId, turn]));
    const path: Turn[] = [];
    let currentId = this.branchLeafTurnId(resolvedBranchId);

    while (currentId) {
      const turn = byId.get(currentId);
      if (!turn) break;
      path.push(turn);
      currentId = turn.parentTurnId;
    }

    return path.reverse();
  }

  // -- Internal --

  private getTurn(turnId: string): Turn {
    const turn = this.turns.find((t) => t.turnId === turnId);
    if (!turn) throw new Error(`Turn ${turnId} not found in session ${this.sessionId}`);
    return turn;
  }

  private getBranch(branchId: string): Branch {
    const branch = this.branches.find((b) => b.branchId === branchId);
    if (!branch) throw new Error(`Branch ${branchId} not found in session ${this.sessionId}`);
    return branch;
  }

  private branchLeafTurnId(branchId: string): string {
    const branch = this.getBranch(branchId);
    for (let i = this.turns.length - 1; i >= 0; i -= 1) {
      if (this.turns[i].branchId === branchId) return this.turns[i].turnId;
    }
    return branch.parentTurnId;
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

  toJSON(): Record<string, unknown> {
    return {
      sessionId: this.sessionId, goal: this.goal, status: this.status,
      summary: this.summary, metadata: this.metadata,
      activeBranchId: this.activeBranchId,
      activeTurnId: this.activeTurnId,
      branches: this.branches.map((branch) => branch.toJSON()),
      turns: this.turns.map((t) => t.toJSON()),
      events: this.events,
      createdAt: this.createdAt, updatedAt: this.updatedAt,
    };
  }

  static fromJSON(data: Record<string, unknown>): Session {
    const s = new Session({ goal: readString(data, "goal"), metadata: readRecord(data, "metadata") ?? {} });
    const branchRecords = readRecordArray(data, "branches");
    const activeBranchId = readString(data, "activeBranchId", "main");
    const activeTurnId = readString(data, "activeTurnId");
    Object.assign(s, {
      sessionId: readString(data, "sessionId", s.sessionId),
      status: readSessionStatus(data, "status"),
      summary: readString(data, "summary"),
      activeBranchId,
      activeTurnId,
      createdAt: readString(data, "createdAt", s.createdAt),
      updatedAt: readString(data, "updatedAt"),
    });
    s.branches.splice(0, s.branches.length);
    if (branchRecords.length === 0) branchRecords.push({ branchId: "main", label: "Main" });
    for (const bd of branchRecords) s.branches.push(Branch.fromJSON(bd));
    const turnRecords = readRecordArray(data, "turns");
    const hasTurnLineage = turnRecords.some(
      (turn) => readString(turn, "parentTurnId") !== "" || readString(turn, "branchId") !== "",
    );
    const shouldSynthesizeMainLineage = branchRecords.length === 1
      && activeBranchId === "main"
      && activeTurnId === ""
      && !hasTurnLineage;
    let previousMainTurnId = "";
    for (const td of turnRecords) {
      const turn = Turn.fromJSON(td, shouldSynthesizeMainLineage
        ? { parentTurnId: previousMainTurnId, branchId: "main" }
        : {});
      s.turns.push(turn);
      if (shouldSynthesizeMainLineage) previousMainTurnId = turn.turnId;
    }
    if (s.activeTurnId === "") {
      s.activeTurnId = s.branchLeafTurnId(s.activeBranchId);
    }
    for (const eventData of readRecordArray(data, "events")) {
      const event = readSessionEvent(eventData);
      if (event) s.events.push(event);
    }
    return s;
  }
}
