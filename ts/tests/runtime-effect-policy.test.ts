import { describe, expect, it, vi } from "vitest";

import {
  RuntimeEffectExecutionMode,
  RuntimeEffectPolicy,
  RuntimeEffectPolicyError,
  type RuntimeEffectDeclaration,
} from "../src/runtimes/effect-policy.js";
import { activateRuntimeComponent } from "../src/runtimes/component-lifecycle.js";
import {
  createInMemoryWorkspaceEnv,
  defineRuntimeCommand,
  type RuntimeGrantEvent,
  type RuntimeToolGrant,
} from "../src/runtimes/workspace-env.js";

describe("RuntimeEffectPolicy", () => {
  it("requires an available external sandbox for untrusted components", () => {
    expect(() => new RuntimeEffectPolicy({
      mode: RuntimeEffectExecutionMode.CANDIDATE,
      untrustedComponent: true,
    })).toThrowError(expect.objectContaining({ code: "external_sandbox_required" }));

    expect(() => new RuntimeEffectPolicy({
      mode: RuntimeEffectExecutionMode.CANDIDATE,
      untrustedComponent: true,
      sandbox: { boundary: "in_process", available: true },
    })).toThrowError(expect.objectContaining({ code: "external_sandbox_required" }));

    expect(() => new RuntimeEffectPolicy({
      mode: RuntimeEffectExecutionMode.CANDIDATE,
      untrustedComponent: true,
      sandbox: { boundary: "process", available: false },
    })).toThrowError(expect.objectContaining({ code: "external_sandbox_unavailable" }));

    expect(() => new RuntimeEffectPolicy({
      mode: RuntimeEffectExecutionMode.CANDIDATE,
      untrustedComponent: true,
      sandbox: { boundary: "process", available: true },
    })).not.toThrow();
  });

  it("fails closed before invoking an effect whose metadata is absent", async () => {
    const execute = vi.fn(() => ({ stdout: "should-not-run", stderr: "", exitCode: 0 }));
    const events: RuntimeGrantEvent[] = [];
    const env = await createInMemoryWorkspaceEnv().scope({
      effectPolicy: new RuntimeEffectPolicy({ mode: RuntimeEffectExecutionMode.CANDIDATE }),
      grantEventSink: { onRuntimeGrantEvent: (event) => events.push(event) },
      commands: [defineRuntimeCommand("publish", execute)],
    });

    await expect(env.exec("publish secret-argument")).rejects.toMatchObject({
      code: "effect_metadata_required",
    });

    expect(execute).not.toHaveBeenCalled();
    expect(events).toMatchObject([{
      kind: "command",
      phase: "error",
      name: "publish",
      argsSummary: [],
      error: "effect_metadata_required",
      effectClass: "undeclared",
      effectOutcome: "denied",
    }]);
    expect(JSON.stringify(events)).not.toContain("secret-argument");
  });

  it("denies irreversible candidate effects until an exact commit boundary is authorized", async () => {
    const execute = vi.fn(() => ({ stdout: "published", stderr: "", exitCode: 0 }));
    const command = defineRuntimeCommand("publish", execute, {
      effect: { effectClass: "irreversible", commitBoundary: "release:42" },
    });
    const base = createInMemoryWorkspaceEnv();
    const denied = await base.scope({
      effectPolicy: new RuntimeEffectPolicy({
        mode: RuntimeEffectExecutionMode.CANDIDATE,
        allowIrreversible: true,
        committed: true,
        commitBoundaryId: "release:41",
      }),
      commands: [command],
    });

    await expect(denied.exec("publish")).rejects.toMatchObject({
      code: "irreversible_effect_requires_commit",
    });
    expect(execute).not.toHaveBeenCalled();

    const committed = await base.scope({
      effectPolicy: new RuntimeEffectPolicy({
        mode: RuntimeEffectExecutionMode.CANDIDATE,
        allowIrreversible: true,
        committed: true,
        commitBoundaryId: "release:42",
      }),
      commands: [command],
    });
    await expect(committed.exec("publish")).resolves.toMatchObject({ exitCode: 0 });
    expect(execute).toHaveBeenCalledOnce();
  });

  it("owns reversible effect cleanup in the invoking component scope", async () => {
    const dispose = vi.fn();
    const componentScope = await activateRuntimeComponent(
      { componentId: "extension:publisher" },
      () => undefined,
    );
    const env = await createInMemoryWorkspaceEnv().scope({
      componentScope,
      effectPolicy: new RuntimeEffectPolicy({ mode: RuntimeEffectExecutionMode.SHADOW }),
      commands: [
        defineRuntimeCommand("subscribe", () => ({ stdout: "ready", stderr: "", exitCode: 0 }), {
          effect: { effectClass: "reversible", disposer: dispose },
        }),
      ],
    });

    await env.exec("subscribe");
    expect(dispose).not.toHaveBeenCalled();
    await componentScope.dispose();
    expect(dispose).toHaveBeenCalledOnce();
  });

  it("owns compensations only when durable recovery metadata is complete", async () => {
    const compensate = vi.fn();
    const componentScope = await activateRuntimeComponent(
      { componentId: "extension:billing" },
      () => undefined,
    );
    const tool: RuntimeToolGrant = {
      kind: "tool",
      name: "reserve-credit",
      effect: {
        effectClass: "compensatable",
        compensation: {
          compensate,
          idempotencyKey: "reservation:42",
          observationalEquivalence: "reservation 42 is absent",
          journaledBeforeInvoke: true,
        },
      },
      execute: () => ({ text: "reserved" }),
    };
    const env = await createInMemoryWorkspaceEnv().scope({
      componentScope,
      effectPolicy: new RuntimeEffectPolicy({ mode: RuntimeEffectExecutionMode.CANARY }),
      tools: [tool],
    });

    await env.tools?.[0]?.execute?.({ account: "private-account" });
    await componentScope.dispose();
    expect(compensate).toHaveBeenCalledOnce();

    const invalid = {
      effectClass: "compensatable",
      compensation: {
        compensate,
        idempotencyKey: "",
        observationalEquivalence: "reservation is absent",
        journaledBeforeInvoke: true,
      },
    } as unknown as RuntimeEffectDeclaration;
    await expect(createInMemoryWorkspaceEnv().scope({
      tools: [{ kind: "tool", name: "invalid", effect: invalid }],
    })).rejects.toMatchObject({ code: "compensation_metadata_required" });
  });

  it("requires a component scope for reversible and compensatable effects", () => {
    const policy = new RuntimeEffectPolicy({ mode: RuntimeEffectExecutionMode.ACTIVE });

    expect(() => policy.authorize({
      effectClass: "reversible",
      disposer: () => undefined,
    })).toThrowError(expect.objectContaining({ code: "component_scope_required" }));
  });

  it("records only the effect class and safe outcome for classified calls", async () => {
    const events: RuntimeGrantEvent[] = [];
    const componentScope = await activateRuntimeComponent(
      { componentId: "extension:audit" },
      () => undefined,
    );
    const env = await createInMemoryWorkspaceEnv().scope({
      componentScope,
      effectPolicy: new RuntimeEffectPolicy({ mode: RuntimeEffectExecutionMode.CANDIDATE }),
      grantEventSink: { onRuntimeGrantEvent: (event) => events.push(event) },
      tools: [{
        kind: "tool",
        name: "classified-tool",
        provenance: { source: "secret-source" },
        effect: { effectClass: "reversible", disposer: () => undefined },
        execute: () => ({ text: "secret-result" }),
      }],
    });

    await env.tools?.[0]?.execute?.({ token: "secret-input" });

    expect(events).toMatchObject([
      { phase: "start", effectClass: "reversible", effectOutcome: "allowed" },
      { phase: "end", effectClass: "reversible", effectOutcome: "completed" },
    ]);
    const audit = JSON.stringify(events);
    expect(audit).not.toContain("secret-input");
    expect(audit).not.toContain("secret-result");
    expect(audit).not.toContain("secret-source");
    await componentScope.dispose();
  });

  it("uses stable policy errors rather than candidate-owned error text", () => {
    const error = new RuntimeEffectPolicyError("effect_metadata_required");
    expect(error.message).toBe("runtime effect metadata is required by policy");
  });
});
