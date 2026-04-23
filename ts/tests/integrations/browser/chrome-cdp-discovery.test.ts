import { describe, expect, test } from "vitest";

import {
  ChromeCdpDiscoveryError,
  ChromeCdpTargetDiscovery,
  selectChromeCdpTarget,
} from "../../../src/integrations/browser/chrome-cdp-discovery.js";
import { buildDefaultBrowserSessionConfig } from "../../../src/integrations/browser/policy.js";

describe("chrome cdp discovery", () => {
  test("selects the preferred allowed target when present", () => {
    const config = buildDefaultBrowserSessionConfig({ allowedDomains: ["example.com"] });
    const target = selectChromeCdpTarget(
      [
        {
          targetId: "target_1",
          targetType: "page",
          title: "Home",
          url: "https://example.com/home",
          webSocketDebuggerUrl: "ws://127.0.0.1:9222/devtools/page/1",
        },
        {
          targetId: "target_2",
          targetType: "page",
          title: "Dashboard",
          url: "https://example.com/dashboard",
          webSocketDebuggerUrl: "ws://127.0.0.1:9222/devtools/page/2",
        },
      ],
      config,
      { preferredUrl: "https://example.com/dashboard" },
    );

    expect(target.targetId).toBe("target_2");
  });

  test("rejects when no debugger target matches the allowlist", () => {
    const config = buildDefaultBrowserSessionConfig({ allowedDomains: ["example.com"] });

    expect(() =>
      selectChromeCdpTarget(
        [
          {
            targetId: "target_1",
            targetType: "page",
            title: "Blocked",
            url: "https://blocked.example.net/home",
            webSocketDebuggerUrl: "ws://127.0.0.1:9222/devtools/page/1",
          },
        ],
        config,
      ),
    ).toThrowError(ChromeCdpDiscoveryError);
  });

  test("fetches /json/list and resolves a websocket url", async () => {
    const seenUrls: string[] = [];
    const discovery = new ChromeCdpTargetDiscovery({
      debuggerUrl: "http://127.0.0.1:9222/",
      fetchFn: async (url) => {
        seenUrls.push(url);
        return {
          ok: true,
          status: 200,
          json: async () => [
            {
              id: "target_1",
              type: "page",
              title: "Dashboard",
              url: "https://example.com/dashboard",
              webSocketDebuggerUrl: "ws://127.0.0.1:9222/devtools/page/1",
            },
          ],
        };
      },
    });
    const config = buildDefaultBrowserSessionConfig({ allowedDomains: ["example.com"] });

    const websocketUrl = await discovery.resolveWebSocketUrl(config);

    expect(seenUrls).toEqual(["http://127.0.0.1:9222/json/list"]);
    expect(websocketUrl).toBe("ws://127.0.0.1:9222/devtools/page/1");
  });
});
