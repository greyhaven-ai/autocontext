/**
 * Remote mission bridge with delegated approval relay (AC-514 TS parity).
 *
 * This domain object intentionally knows nothing about a concrete WebSocket or
 * HTTP implementation. Its caller must provide an authenticator for the actual
 * transport object. Connection and decision authorization are then bound to
 * that object's identity instead of trusting operator/role fields in messages.
 */

import { randomUUID } from "node:crypto";

export const SessionRole = { VIEWER: "viewer", CONTROLLER: "controller" } as const;
export type SessionRole = (typeof SessionRole)[keyof typeof SessionRole];

export interface AuthenticatedRemoteIdentity {
  /** Stable identity from the authentication system, not a display name. */
  readonly principalId: string;
  /** Human-readable operator name used in operator-facing status. */
  readonly operator: string;
  readonly role: SessionRole;
  /** Mission authorization scope asserted by the trusted authenticator. */
  readonly missionId: string;
  /** Stable ID for the authenticated transport/session being evaluated. */
  readonly transportSessionId: string;
}

export type RemoteTransportAuthenticator<TTransport extends object> = (
  transport: TTransport,
  missionId: string,
) => AuthenticatedRemoteIdentity | null | undefined;

export interface RemoteBridgeOptions<TTransport extends object> {
  readonly authenticate: RemoteTransportAuthenticator<TTransport>;
}

export class RemoteSession {
  readonly remoteSessionId: string;
  readonly sessionId: string;
  readonly principalId: string;
  readonly transportSessionId: string;
  readonly operator: string;
  readonly role: SessionRole;

  private constructor(identity: AuthenticatedRemoteIdentity) {
    this.remoteSessionId = randomUUID().slice(0, 12);
    this.sessionId = identity.missionId;
    this.principalId = identity.principalId;
    this.transportSessionId = identity.transportSessionId;
    this.operator = identity.operator;
    this.role = identity.role;
    Object.freeze(this);
  }

  static createAuthenticated(identity: AuthenticatedRemoteIdentity): RemoteSession {
    return new RemoteSession(validateIdentity(identity));
  }

  get canApprove(): boolean { return this.role === SessionRole.CONTROLLER; }
  get canControl(): boolean { return this.role === SessionRole.CONTROLLER; }
}

type ApprovalDecision =
  | { readonly kind: "approve"; readonly principalId: string; readonly operator: string }
  | { readonly kind: "deny"; readonly principalId: string; readonly operator: string; readonly reason: string }
  | { readonly kind: "timeout" };

interface ApprovalState {
  status: string;
  decidedBy: string;
  decidedByPrincipal: string;
  denialReason: string;
}

const APPROVAL_STATES = new WeakMap<ApprovalRequest, ApprovalState>();

export class ApprovalRequest {
  readonly requestId: string;
  readonly action: string;

  private constructor(action: string) {
    this.requestId = randomUUID().slice(0, 12);
    this.action = action;
    APPROVAL_STATES.set(this, {
      status: "pending",
      decidedBy: "",
      decidedByPrincipal: "",
      denialReason: "",
    });
    Object.freeze(this);
  }

  static create(action: string): ApprovalRequest {
    const normalized = action.trim();
    if (!normalized) throw new Error("Approval action must not be empty");
    return new ApprovalRequest(normalized);
  }

  get status(): string { return approvalState(this).status; }
  get decidedBy(): string { return approvalState(this).decidedBy; }
  get decidedByPrincipal(): string { return approvalState(this).decidedByPrincipal; }
  get denialReason(): string { return approvalState(this).denialReason; }
}

interface BoundRemoteSession<TTransport extends object> {
  readonly transport: TTransport;
  readonly session: RemoteSession;
}

export class RemoteBridge<TTransport extends object> {
  readonly missionId: string;
  private readonly authenticate: RemoteTransportAuthenticator<TTransport>;
  private readonly sessions = new Map<string, BoundRemoteSession<TTransport>>();
  private readonly sessionByTransport = new WeakMap<TTransport, RemoteSession>();
  private readonly approvals = new Map<string, ApprovalRequest>();

  constructor(missionId: string, options: RemoteBridgeOptions<TTransport>) {
    const normalized = missionId.trim();
    if (!normalized) throw new Error("Mission ID must not be empty");
    if (!options || typeof options.authenticate !== "function") {
      throw new Error("RemoteBridge requires an authenticated transport resolver");
    }
    this.missionId = normalized;
    this.authenticate = options.authenticate;
  }

  connect(transport: TTransport): RemoteSession {
    if (this.sessionByTransport.has(transport)) {
      throw new Error("Transport is already connected to this remote bridge");
    }
    const identity = this.requireAuthenticatedIdentity(transport);
    const session = RemoteSession.createAuthenticated(identity);
    this.sessions.set(session.remoteSessionId, { transport, session });
    this.sessionByTransport.set(transport, session);
    return session;
  }

  disconnect(transport: TTransport): void {
    const session = this.sessionByTransport.get(transport);
    if (!session) return;
    this.sessions.delete(session.remoteSessionId);
    this.sessionByTransport.delete(transport);
  }

  get connectedSessions(): RemoteSession[] {
    return [...this.sessions.values()].map(({ session }) => session);
  }

  requestApproval(action: string): ApprovalRequest {
    const request = ApprovalRequest.create(action);
    this.approvals.set(request.requestId, request);
    return request;
  }

  get pendingApprovals(): ApprovalRequest[] {
    return [...this.approvals.values()].filter((approval) => approval.status === "pending");
  }

  respond(
    requestId: string,
    approved: boolean,
    transport: TTransport,
    reason?: string,
  ): void {
    const session = this.requireCurrentBoundSession(transport);
    if (session.role !== SessionRole.CONTROLLER) {
      throw new Error(`Authenticated operator '${session.operator}' is a viewer and cannot respond`);
    }
    const request = this.approvals.get(requestId);
    if (!request) throw new Error(`Approval '${requestId}' not found`);
    applyApprovalDecision(request, approved
      ? {
          kind: "approve",
          principalId: session.principalId,
          operator: session.operator,
        }
      : {
          kind: "deny",
          principalId: session.principalId,
          operator: session.operator,
          reason: reason ?? "",
        });
  }

  timeoutApproval(requestId: string): void {
    const request = this.approvals.get(requestId);
    if (!request) throw new Error(`Approval '${requestId}' not found`);
    applyApprovalDecision(request, { kind: "timeout" });
  }

  private requireCurrentBoundSession(transport: TTransport): RemoteSession {
    const session = this.sessionByTransport.get(transport);
    if (!session) {
      throw new Error("Transport is not connected and cannot respond");
    }
    const current = this.requireAuthenticatedIdentity(transport);
    if (
      current.principalId !== session.principalId
      || current.transportSessionId !== session.transportSessionId
      || current.role !== session.role
      || current.operator !== session.operator
    ) {
      throw new Error("Authenticated transport identity changed; reconnect before responding");
    }
    return session;
  }

  private requireAuthenticatedIdentity(transport: TTransport): AuthenticatedRemoteIdentity {
    const identity = this.authenticate(transport, this.missionId);
    if (!identity) {
      throw new Error("Remote transport is not authenticated or authorized for this mission");
    }
    const validated = validateIdentity(identity);
    if (validated.missionId !== this.missionId) {
      throw new Error("Remote transport is not authorized for this mission");
    }
    return validated;
  }
}

function approvalState(request: ApprovalRequest): ApprovalState {
  const state = APPROVAL_STATES.get(request);
  if (!state) throw new Error("Approval request state is unavailable");
  return state;
}

function applyApprovalDecision(request: ApprovalRequest, decision: ApprovalDecision): void {
  const state = approvalState(request);
  const action = decision.kind === "approve"
    ? "approve request"
    : decision.kind === "deny"
      ? "deny request"
      : "time out request";
  if (state.status !== "pending") {
    throw new Error(`Cannot ${action} once status=${state.status}`);
  }
  if (decision.kind === "timeout") {
    state.status = "timed_out";
    return;
  }
  state.status = decision.kind === "approve" ? "approved" : "denied";
  state.decidedBy = decision.operator;
  state.decidedByPrincipal = decision.principalId;
  if (decision.kind === "deny") state.denialReason = decision.reason;
}

function validateIdentity(identity: AuthenticatedRemoteIdentity): AuthenticatedRemoteIdentity {
  if (!identity || typeof identity !== "object") {
    throw new Error("Remote transport identity is invalid");
  }
  const principalId = requireIdentityString(identity.principalId, "principal ID");
  const operator = requireIdentityString(identity.operator, "operator");
  const missionId = requireIdentityString(identity.missionId, "mission ID");
  const transportSessionId = requireIdentityString(identity.transportSessionId, "transport session ID");
  if (identity.role !== SessionRole.VIEWER && identity.role !== SessionRole.CONTROLLER) {
    throw new Error("Remote transport identity has an invalid role");
  }
  return Object.freeze({
    principalId,
    operator,
    role: identity.role,
    missionId,
    transportSessionId,
  });
}

function requireIdentityString(value: unknown, label: string): string {
  if (typeof value !== "string" || !value.trim() || value.length > 256) {
    throw new Error(`Remote transport identity has an invalid ${label}`);
  }
  return value.trim();
}
