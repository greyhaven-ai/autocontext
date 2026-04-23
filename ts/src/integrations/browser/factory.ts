import { ChromeCdpRuntime } from "./chrome-cdp-runtime.js";
import { resolveBrowserSessionConfig } from "./policy.js";
import type { BrowserRuntimePort, BrowserSettingsLike, BrowserSessionConfig } from "./types.js";

export interface BrowserRuntimeSettingsLike extends BrowserSettingsLike {
  readonly browserEnabled: boolean;
  readonly browserBackend: string;
  readonly runsRoot: string;
}

export interface ConfiguredBrowserRuntime {
  readonly sessionConfig: BrowserSessionConfig;
  readonly runtime: BrowserRuntimePort;
}

export function createBrowserRuntimeFromSettings(
  settings: BrowserRuntimeSettingsLike,
  opts: { evidenceRoot?: string } = {},
): ConfiguredBrowserRuntime | null {
  if (!settings.browserEnabled) {
    return null;
  }

  if (settings.browserBackend !== "chrome-cdp") {
    throw new Error(`unsupported browser backend: ${settings.browserBackend}`);
  }

  return {
    sessionConfig: resolveBrowserSessionConfig(settings),
    runtime: new ChromeCdpRuntime({
      debuggerUrl: settings.browserDebuggerUrl || undefined,
      preferredTargetUrl: settings.browserPreferredTargetUrl || undefined,
      evidenceRoot: opts.evidenceRoot ?? settings.runsRoot,
    }),
  };
}
