import { describe, expect, test } from "vitest";

import { AppSettingsSchema } from "../../../src/config/index.js";
import { ChromeCdpRuntime } from "../../../src/integrations/browser/chrome-cdp-runtime.js";
import {
  createBrowserRuntimeFromSettings,
  type ConfiguredBrowserRuntime,
} from "../../../src/integrations/browser/factory.js";

describe("browser runtime factory", () => {
  test("returns null when browser exploration is disabled", () => {
    const settings = AppSettingsSchema.parse({
      browserEnabled: false,
      runsRoot: "/tmp/runs",
    });

    expect(createBrowserRuntimeFromSettings(settings)).toBeNull();
  });

  test("builds a chrome cdp runtime from settings", () => {
    const settings = AppSettingsSchema.parse({
      browserEnabled: true,
      browserBackend: "chrome-cdp",
      browserAllowedDomains: "example.com",
      browserDebuggerUrl: "http://127.0.0.1:9333",
      browserPreferredTargetUrl: "https://example.com/dashboard",
      runsRoot: "/tmp/runs",
    });

    const configured = createBrowserRuntimeFromSettings(settings);

    expect(configured).not.toBeNull();
    expect((configured as ConfiguredBrowserRuntime).sessionConfig.allowedDomains).toEqual(["example.com"]);
    expect((configured as ConfiguredBrowserRuntime).runtime).toBeInstanceOf(ChromeCdpRuntime);
    expect(((configured as ConfiguredBrowserRuntime).runtime as ChromeCdpRuntime).debuggerUrl).toBe(
      "http://127.0.0.1:9333",
    );
    expect(((configured as ConfiguredBrowserRuntime).runtime as ChromeCdpRuntime).preferredTargetUrl).toBe(
      "https://example.com/dashboard",
    );
    expect(((configured as ConfiguredBrowserRuntime).runtime as ChromeCdpRuntime).evidenceRoot).toBe("/tmp/runs");
  });

  test("rejects unsupported browser backends", () => {
    const settings = AppSettingsSchema.parse({
      browserEnabled: true,
      browserBackend: "mystery",
      runsRoot: "/tmp/runs",
    });

    expect(() => createBrowserRuntimeFromSettings(settings)).toThrowError(
      "unsupported browser backend: mystery",
    );
  });
});
