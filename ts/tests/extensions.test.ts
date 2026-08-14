import { describe, expect, it } from "vitest";
import { mkdtempSync, rmSync, writeFileSync } from "node:fs";
import { tmpdir } from "node:os";
import { join } from "node:path";

import {
  ExtensionAPI,
  HookBus,
  HookEvents,
  HookResult,
  loadExtensionComponents,
  loadExtensions,
} from "../src/extensions/index.js";

describe("TypeScript extension hooks", () => {
  it("runs handlers in order and applies returned payload/metadata", () => {
    const bus = new HookBus();
    const order: string[] = [];

    bus.on(HookEvents.CONTEXT_COMPONENTS, (event) => {
      order.push("first");
      return {
        components: {
          ...readStringRecord(event.payload.components),
          playbook: "hooked playbook",
        },
      };
    });
    bus.on(HookEvents.CONTEXT_COMPONENTS, (event) => {
      order.push("second");
      expect(readStringRecord(event.payload.components).playbook).toBe("hooked playbook");
      return new HookResult({ metadata: { seen: true } });
    });

    const event = bus.emit(HookEvents.CONTEXT_COMPONENTS, {
      components: { playbook: "base" },
    });

    expect(order).toEqual(["first", "second"]);
    expect(event.payload.components).toEqual({ playbook: "hooked playbook" });
    expect(event.metadata.seen).toBe(true);
  });

  it("raises a clear error when a hook blocks an event", () => {
    const bus = new HookBus();
    bus.on(HookEvents.ARTIFACT_WRITE, () => new HookResult({
      block: true,
      reason: "policy rejected artifact",
    }));

    const event = bus.emit(HookEvents.ARTIFACT_WRITE, { path: "runs/r1/out.md" });

    expect(() => event.raiseIfBlocked()).toThrow(/blocked artifact_write: policy rejected artifact/);
  });

  it("loads an extension module and lets it register through the API facade", async () => {
    const root = mkdtempSync(join(tmpdir(), "autoctx-ts-ext-"));
    try {
      const extensionPath = join(root, "hook.mjs");
      writeFileSync(
        extensionPath,
        `
          export function register(api) {
            api.on("context", (event) => ({
              roles: {
                ...event.payload.roles,
                competitor: event.payload.roles.competitor + "\\nloaded extension"
              }
            }));
          }
        `,
        "utf-8",
      );

      const bus = new HookBus();
      const loaded = await loadExtensions(extensionPath, bus);
      const event = bus.emit(HookEvents.CONTEXT, {
        roles: { competitor: "base prompt" },
      });

      expect(loaded).toEqual([extensionPath]);
      expect(event.payload.roles).toEqual({ competitor: "base prompt\nloaded extension" });
    } finally {
      rmSync(root, { recursive: true, force: true });
    }
  });

  it("supports decorator-style registration via ExtensionAPI.on", () => {
    const bus = new HookBus();
    const api = new ExtensionAPI(bus);

    api.on(HookEvents.AFTER_PROVIDER_RESPONSE)((event) => ({
      text: `${event.payload.text} decorated`,
    }));

    const event = api.emit(HookEvents.AFTER_PROVIDER_RESPONSE, { text: "response" });

    expect(event.payload.text).toBe("response decorated");
  });

  it("loads and unloads a lifecycle-owned extension component", async () => {
    const root = mkdtempSync(join(tmpdir(), "autoctx-ts-managed-ext-"));
    try {
      const extensionPath = join(root, "managed-hook.mjs");
      writeFileSync(
        extensionPath,
        `
          export function register(api) {
            api.on("context", () => ({ managed: true }));
          }
        `,
        "utf-8",
      );
      const bus = new HookBus();
      const [component] = await loadExtensionComponents(extensionPath, bus);

      expect(bus.emit(HookEvents.CONTEXT).payload.managed).toBe(true);
      expect(bus.loadedExtensions).toEqual([extensionPath]);

      await component.unload();

      expect(bus.emit(HookEvents.CONTEXT).payload.managed).toBeUndefined();
      expect(bus.loadedExtensions).toEqual([]);
    } finally {
      rmSync(root, { recursive: true, force: true });
    }
  });

  it("unwinds the successful prefix when a later extension fails to load", async () => {
    const root = mkdtempSync(join(tmpdir(), "autoctx-ts-extension-batch-"));
    try {
      const firstPath = join(root, "first.mjs");
      const failingPath = join(root, "failing.mjs");
      writeFileSync(
        firstPath,
        `export function register(api) { api.on("context", () => ({ leaked: true })); }`,
        "utf-8",
      );
      writeFileSync(
        failingPath,
        `export function register(api) {
          api.on("context", () => ({ partial: true }));
          throw new Error("batch activation failed");
        }`,
        "utf-8",
      );
      const bus = new HookBus();

      await expect(loadExtensionComponents([firstPath, failingPath], bus))
        .rejects.toThrow("batch activation failed");

      expect(bus.loadedExtensions).toEqual([]);
      expect(bus.hasHandlers(HookEvents.CONTEXT)).toBe(false);
      expect(bus.emit(HookEvents.CONTEXT).payload).toEqual({});
    } finally {
      rmSync(root, { recursive: true, force: true });
    }
  });
});

function readStringRecord(value: unknown): Record<string, string> {
  if (typeof value !== "object" || value === null || Array.isArray(value)) {
    return {};
  }
  const result: Record<string, string> = {};
  for (const [key, raw] of Object.entries(value)) {
    if (typeof raw === "string") {
      result[key] = raw;
    }
  }
  return result;
}
