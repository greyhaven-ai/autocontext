import { describe, expect, it } from "vitest";

import {
  SANDBOX_CAPABILITY_NAMES,
  lifecycleHooksForBootMode,
  normalizeSandboxAdapterCapabilities,
  planSandboxStartup,
} from "../src/execution/sandbox-adapter-contracts.ts";
import { buildSandboxCapabilitySessionEvent } from "../src/session/background-session-events.js";

const timestamp = "2026-06-01T00:07:00.000Z";
const sessionId = "run:run-123:runtime";

class LocalAdapter {}

class RemoteAdapter {
  readonly sandboxCapabilities = {
    snapshot: true,
    restore: true,
    prebuild_repo_image: false,
    warm: true,
    resolve_tunnel_ports: true,
    provider_internal: true,
  };
}

describe("sandbox adapter capability contracts", () => {
  it("defaults capabilities to false for local execution", () => {
    expect(SANDBOX_CAPABILITY_NAMES).toEqual([
      "snapshot",
      "restore",
      "prebuild_repo_image",
      "warm",
      "resolve_tunnel_ports",
    ]);
    expect(normalizeSandboxAdapterCapabilities(new LocalAdapter())).toEqual({
      snapshot: false,
      restore: false,
      prebuild_repo_image: false,
      warm: false,
      resolve_tunnel_ports: false,
    });
  });

  it("detects advertised capabilities and ignores unknown provider fields", () => {
    expect(normalizeSandboxAdapterCapabilities(new RemoteAdapter())).toEqual({
      snapshot: true,
      restore: true,
      prebuild_repo_image: false,
      warm: true,
      resolve_tunnel_ports: true,
    });
  });

  it("plans restored startup from advertised capability and skips setup", () => {
    expect(
      planSandboxStartup({
        sessionId,
        requestedBootMode: "restore",
        capabilities: new RemoteAdapter(),
        snapshotId: "snap-123",
      }),
    ).toEqual({
      session_id: sessionId,
      requested_boot_mode: "restore",
      boot_mode: "restored",
      capability: "restore",
      supported: true,
      degraded: false,
      terminal: false,
      unsupported_policy: "degrade_to_fresh",
      reason: "",
      lifecycle_hooks: ["start"],
    });
  });

  it("requires a snapshot ref for restored startup even when the capability is advertised", () => {
    expect(
      planSandboxStartup({
        sessionId,
        requestedBootMode: "restore",
        capabilities: new RemoteAdapter(),
      }),
    ).toEqual({
      session_id: sessionId,
      requested_boot_mode: "restore",
      boot_mode: "fresh",
      capability: "restore",
      supported: true,
      degraded: true,
      terminal: false,
      unsupported_policy: "degrade_to_fresh",
      reason: "missing_snapshot_ref",
      lifecycle_hooks: ["setup", "start"],
    });

    expect(
      planSandboxStartup({
        sessionId,
        requestedBootMode: "restore",
        capabilities: new RemoteAdapter(),
        unsupportedPolicy: "fail_closed",
      }),
    ).toEqual({
      session_id: sessionId,
      requested_boot_mode: "restore",
      boot_mode: "restored",
      capability: "restore",
      supported: true,
      degraded: false,
      terminal: true,
      unsupported_policy: "fail_closed",
      reason: "missing_snapshot_ref",
      lifecycle_hooks: [],
    });
  });

  it("maps warm and repo-image requests to optional capabilities", () => {
    const warmPlan = planSandboxStartup({
      sessionId,
      requestedBootMode: "warm",
      capabilities: new RemoteAdapter(),
      provider: "remote",
    });
    expect(warmPlan.boot_mode).toBe("warmed");
    expect(warmPlan.capability).toBe("warm");
    expect(warmPlan.supported).toBe(true);
    expect(warmPlan.lifecycle_hooks).toEqual(["start"]);

    const repoImagePlan = planSandboxStartup({
      sessionId,
      requestedBootMode: "repo_image",
      capabilities: new RemoteAdapter(),
      repoImageId: "repo-img-1",
    });
    expect(repoImagePlan.boot_mode).toBe("fresh");
    expect(repoImagePlan.capability).toBe("prebuild_repo_image");
    expect(repoImagePlan.degraded).toBe(true);
    expect(repoImagePlan.reason).toBe("unsupported_prebuild_repo_image");
  });

  it("degrades or fails closed when a requested capability is missing", () => {
    expect(
      planSandboxStartup({
        sessionId,
        requestedBootMode: "restore",
        capabilities: new LocalAdapter(),
        snapshotId: "snap-123",
        unsupportedPolicy: "degrade_to_fresh",
      }),
    ).toEqual({
      session_id: sessionId,
      requested_boot_mode: "restore",
      boot_mode: "fresh",
      capability: "restore",
      supported: false,
      degraded: true,
      terminal: false,
      unsupported_policy: "degrade_to_fresh",
      reason: "unsupported_restore",
      lifecycle_hooks: ["setup", "start"],
    });

    expect(
      planSandboxStartup({
        sessionId,
        requestedBootMode: "restore",
        capabilities: new LocalAdapter(),
        snapshotId: "snap-123",
        unsupportedPolicy: "fail_closed",
      }),
    ).toEqual({
      session_id: sessionId,
      requested_boot_mode: "restore",
      boot_mode: "restored",
      capability: "restore",
      supported: false,
      degraded: false,
      terminal: true,
      unsupported_policy: "fail_closed",
      reason: "unsupported_restore",
      lifecycle_hooks: [],
    });
  });

  it("ties default lifecycle hooks to boot mode", () => {
    expect(lifecycleHooksForBootMode("fresh")).toEqual(["setup", "start"]);
    expect(lifecycleHooksForBootMode("build")).toEqual(["setup", "start"]);
    expect(lifecycleHooksForBootMode("restored")).toEqual(["start"]);
    expect(lifecycleHooksForBootMode("repo_image")).toEqual(["start"]);
    expect(lifecycleHooksForBootMode("warmed")).toEqual(["start"]);
  });

  it("drops free-form reason text from sandbox capability events", () => {
    const event = buildSandboxCapabilitySessionEvent({
      sessionId,
      sequence: 69,
      timestamp,
      capability: "restore",
      phase: "failed",
      bootMode: "restored",
      provider: "primeintellect",
      unsupportedPolicy: "fail_closed",
      reason: "TOKEN=SECRET_VALUE",
      degraded: false,
      error: "also SECRET_VALUE",
    });

    expect(event.payload_summary).not.toHaveProperty("reason");
    expect(JSON.stringify(event)).not.toContain("SECRET_VALUE");
  });

  it("records sandbox capability failures without raw adapter errors", () => {
    const event = buildSandboxCapabilitySessionEvent({
      sessionId,
      sequence: 70,
      timestamp,
      capability: "restore",
      phase: "failed",
      bootMode: "restored",
      provider: "primeintellect",
      unsupportedPolicy: "fail_closed",
      reason: "adapter_error",
      degraded: false,
      error: "TOKEN=SECRET_VALUE",
    });

    expect(event).toEqual({
      event_id: "sandbox:run:run-123:runtime:restore:failed:70",
      session_id: sessionId,
      sequence: 70,
      ts: timestamp,
      event: "session_status",
      source_event_type: "sandbox_capability",
      status: "failed",
      title: "Sandbox restore failed",
      payload_summary: {
        capability: "restore",
        phase: "failed",
        boot_mode: "restored",
        provider: "primeintellect",
        unsupported_policy: "fail_closed",
        reason: "adapter_error",
        degraded: false,
      },
    });
    expect(JSON.stringify(event)).not.toContain("SECRET_VALUE");
  });
});
