import { describe, expect, it } from "vitest";

import {
  ApprovalRequest,
  type AuthenticatedRemoteIdentity,
  RemoteBridge,
  RemoteSession,
  SessionRole,
} from "../src/session/remote-bridge.js";

interface TestTransport {
  readonly connectionId: string;
  readonly claimedOperator?: string;
  readonly claimedRole?: string;
}

function identity(overrides: Partial<AuthenticatedRemoteIdentity> = {}): AuthenticatedRemoteIdentity {
  return {
    principalId: "principal-bob",
    operator: "bob",
    role: SessionRole.CONTROLLER,
    missionId: "m1",
    transportSessionId: "transport-bob",
    ...overrides,
  };
}

function bridgeHarness() {
  const identities = new WeakMap<TestTransport, AuthenticatedRemoteIdentity>();
  const bridge = new RemoteBridge<TestTransport>("m1", {
    authenticate: (transport) => identities.get(transport),
  });
  return { bridge, identities };
}

describe("RemoteSession", () => {
  it("derives capabilities from a validated authenticated identity", () => {
    const viewer = RemoteSession.createAuthenticated(identity({
      principalId: "principal-alice",
      operator: "alice",
      role: SessionRole.VIEWER,
      transportSessionId: "transport-alice",
    }));
    const controller = RemoteSession.createAuthenticated(identity());

    expect(viewer.canApprove).toBe(false);
    expect(controller.canApprove).toBe(true);
    expect(Object.isFrozen(controller)).toBe(true);
  });
});

describe("ApprovalRequest", () => {
  it("cannot be approved directly outside the bridge authority", () => {
    const request = ApprovalRequest.create("deploy");
    expect(request.status).toBe("pending");
    expect("approve" in request).toBe(false);
    expect("deny" in request).toBe(false);
  });
});

describe("RemoteBridge", () => {
  it("fails closed when the transport has no authenticated identity", () => {
    const { bridge } = bridgeHarness();
    const transport = { connectionId: "untrusted" };

    expect(() => bridge.connect(transport)).toThrow("not authenticated");
    expect(bridge.connectedSessions).toHaveLength(0);
  });

  it("ignores self-asserted message identity and uses the transport authenticator", () => {
    const { bridge, identities } = bridgeHarness();
    const transport: TestTransport = {
      connectionId: "viewer-connection",
      claimedOperator: "admin",
      claimedRole: SessionRole.CONTROLLER,
    };
    identities.set(transport, identity({
      principalId: "principal-alice",
      operator: "alice",
      role: SessionRole.VIEWER,
      transportSessionId: "viewer-connection",
    }));

    const session = bridge.connect(transport);
    const request = bridge.requestApproval("deploy");

    expect(session.operator).toBe("alice");
    expect(session.role).toBe(SessionRole.VIEWER);
    expect(() => bridge.respond(request.requestId, true, transport)).toThrow("viewer");
    expect(request.status).toBe("pending");
  });

  it("records decisions under the authenticated stable principal", () => {
    const { bridge, identities } = bridgeHarness();
    const transport = { connectionId: "controller-connection" };
    identities.set(transport, identity({ transportSessionId: transport.connectionId }));
    bridge.connect(transport);
    const request = bridge.requestApproval("deploy");

    bridge.respond(request.requestId, true, transport);

    expect(request.status).toBe("approved");
    expect(request.decidedBy).toBe("bob");
    expect(request.decidedByPrincipal).toBe("principal-bob");
    expect(bridge.pendingApprovals).toHaveLength(0);
  });

  it("does not authorize an unbound transport that claims the same operator", () => {
    const { bridge, identities } = bridgeHarness();
    const bound = { connectionId: "bound" };
    const attacker: TestTransport = {
      connectionId: "attacker",
      claimedOperator: "bob",
      claimedRole: SessionRole.CONTROLLER,
    };
    identities.set(bound, identity({ transportSessionId: bound.connectionId }));
    identities.set(attacker, identity({ transportSessionId: attacker.connectionId }));
    bridge.connect(bound);
    const request = bridge.requestApproval("deploy");

    expect(() => bridge.respond(request.requestId, true, attacker)).toThrow("not connected");
    expect(request.status).toBe("pending");
  });

  it("re-authenticates before a decision and rejects revocation or identity changes", () => {
    const { bridge, identities } = bridgeHarness();
    const revoked = { connectionId: "revoked" };
    identities.set(revoked, identity({ transportSessionId: revoked.connectionId }));
    bridge.connect(revoked);
    const revokedRequest = bridge.requestApproval("deploy one");
    identities.delete(revoked);

    expect(() => bridge.respond(revokedRequest.requestId, true, revoked)).toThrow("not authenticated");

    const changed = { connectionId: "changed" };
    identities.set(changed, identity({ transportSessionId: changed.connectionId }));
    bridge.connect(changed);
    const changedRequest = bridge.requestApproval("deploy two");
    identities.set(changed, identity({
      role: SessionRole.VIEWER,
      transportSessionId: changed.connectionId,
    }));

    expect(() => bridge.respond(changedRequest.requestId, true, changed)).toThrow("identity changed");
    expect(changedRequest.status).toBe("pending");
  });

  it("rejects identities scoped to a different mission", () => {
    const { bridge, identities } = bridgeHarness();
    const transport = { connectionId: "wrong-mission" };
    identities.set(transport, identity({
      missionId: "m2",
      transportSessionId: transport.connectionId,
    }));

    expect(() => bridge.connect(transport)).toThrow("not authorized for this mission");
  });

  it("denies, times out, and keeps decisions terminal", () => {
    const { bridge, identities } = bridgeHarness();
    const transport = { connectionId: "controller" };
    identities.set(transport, identity({ transportSessionId: transport.connectionId }));
    bridge.connect(transport);

    const denied = bridge.requestApproval("deploy");
    bridge.respond(denied.requestId, false, transport, "Not ready");
    expect(denied.status).toBe("denied");
    expect(denied.denialReason).toBe("Not ready");
    expect(() => bridge.respond(denied.requestId, true, transport)).toThrow("status=denied");

    const timedOut = bridge.requestApproval("release");
    bridge.timeoutApproval(timedOut.requestId);
    expect(timedOut.status).toBe("timed_out");
    expect(() => bridge.respond(timedOut.requestId, true, transport)).toThrow("status=timed_out");
  });

  it("disconnect removes only the transport-bound session", () => {
    const { bridge, identities } = bridgeHarness();
    const transport = { connectionId: "controller" };
    identities.set(transport, identity({ transportSessionId: transport.connectionId }));
    bridge.connect(transport);

    bridge.disconnect(transport);

    expect(bridge.connectedSessions).toHaveLength(0);
    const request = bridge.requestApproval("deploy");
    expect(() => bridge.respond(request.requestId, true, transport)).toThrow("not connected");
  });
});
