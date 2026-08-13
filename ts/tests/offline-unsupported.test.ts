/**
 * AC-938: the TypeScript engine refuses offline mode rather than faking it.
 *
 * AC-917 enforces one rule in Python: the engine never initiates an outbound
 * connection. This engine has roughly six unguarded engine-initiated egress
 * sites and no socket-level proof, so setting the flag here would give an
 * operator a guarantee nobody is keeping.
 *
 * Refusing is the honest behavior, and these pin it: an unenforced guarantee
 * that looks enforced is worse than an absent one, because the operator finds
 * out from a packet capture rather than from us.
 */
import { describe, expect, it } from "vitest";

import {
  OFFLINE_UNSUPPORTED_MESSAGE,
  OfflineUnsupportedError,
  assertOfflineSupported,
  offlineRequested,
} from "../src/config/offline.js";
import { buildSettingsAssemblyInput } from "../src/config/settings-assembly-workflow.js";

describe("offline mode on the TypeScript engine", () => {
  it.each(["1", "true", "TRUE", "yes", "on", " 1 "])("refuses when set to %s", (value) => {
    expect(() => assertOfflineSupported({ AUTOCONTEXT_OFFLINE: value })).toThrow(
      OfflineUnsupportedError,
    );
  });

  it.each(["", "0", "false", "no", "off", undefined])("does not refuse for %s", (value) => {
    // The default path must be untouched: this is a refusal, not a new
    // requirement to opt out of.
    expect(() => assertOfflineSupported({ AUTOCONTEXT_OFFLINE: value })).not.toThrow();
    expect(offlineRequested({ AUTOCONTEXT_OFFLINE: value })).toBe(false);
  });

  it("names the engine and where the guarantee does hold", () => {
    // A bare "unsupported" leaves the operator guessing whether they hit a bug,
    // a typo, or a real limitation.
    expect(OFFLINE_UNSUPPORTED_MESSAGE).toContain("TypeScript engine");
    expect(OFFLINE_UNSUPPORTED_MESSAGE).toContain("Python engine");
    expect(OFFLINE_UNSUPPORTED_MESSAGE).toContain("AC-938");
  });

  it("refuses through settings assembly, which every run passes through", () => {
    // The load-bearing case. Guarding a CLI entry point instead would leave a
    // second entry point added later to bypass it silently.
    expect(() => buildSettingsAssemblyInput({ env: { AUTOCONTEXT_OFFLINE: "1" } })).toThrow(
      OfflineUnsupportedError,
    );
  });

  it("assembles settings normally when offline is not requested", () => {
    const assembled = buildSettingsAssemblyInput({ env: {} });
    expect(assembled).toHaveProperty("configuredFields");
  });
});
