import { describe, expect, it, vi } from "vitest";

import {
  assertPublicHttpUrl,
  assertSafeOutboundUrl,
  createPinnedLookup,
  createSafeOutboundFetch,
  isGlobalUnicastIp,
  type OutboundFetch,
  type OutboundHostResolver,
} from "../src/security/outbound-url-policy.js";

const publicResolver: OutboundHostResolver = async () => [
  { address: "93.184.216.34", family: 4 },
  { address: "2606:4700:4700::1111", family: 6 },
];

describe("outbound URL policy", () => {
  it("allows only credential-free HTTP(S) URLs", () => {
    expect(assertPublicHttpUrl("https://example.com/path").href).toBe("https://example.com/path");
    expect(assertPublicHttpUrl("http://93.184.216.34/rpc").href).toBe("http://93.184.216.34/rpc");
    expect(() => assertPublicHttpUrl("file:///etc/passwd")).toThrow("http: or https:");
    expect(() => assertPublicHttpUrl("https://user:secret@example.com/")).toThrow("embedded credentials");
    expect(() => assertPublicHttpUrl("https://example.com/#secret")).toThrow("fragment");
    expect(() => assertPublicHttpUrl("not a url")).toThrow("valid http(s) URL");
  });

  it("does not attach deadline listeners for an invalid request URL", async () => {
    const controller = new AbortController();
    const addListener = vi.spyOn(controller.signal, "addEventListener");
    const safeFetch = createSafeOutboundFetch({
      fetch: async () => new Response("unreachable"),
      resolveHostname: publicResolver,
      requestTimeoutMs: 60_000,
    });

    await expect(safeFetch("file:///etc/passwd", { signal: controller.signal }))
      .rejects.toThrow("http: or https:");
    expect(addListener).not.toHaveBeenCalled();
  });

  it("rejects Host overrides before hostname resolution or fetch", async () => {
    const resolver = vi.fn(publicResolver);
    const baseFetch = vi.fn<OutboundFetch>(async () => new Response("unreachable"));
    const safeFetch = createSafeOutboundFetch({
      fetch: baseFetch,
      resolveHostname: resolver,
    });

    await expect(safeFetch("https://example.com/", {
      headers: { Host: "169.254.169.254" },
    })).rejects.toThrow("must not override the URL host");
    expect(resolver).not.toHaveBeenCalled();
    expect(baseFetch).not.toHaveBeenCalled();
  });

  it.each([
    "http://localhost/",
    "http://service.localhost./",
    "http://127.0.0.1/",
    "http://127.1/",
    "http://2130706433/",
    "http://10.1.2.3/",
    "http://100.64.0.1/",
    "http://100.100.100.200/",
    "http://147.75.207.207/",
    "http://168.63.129.16/",
    "http://169.254.169.254/latest/meta-data/",
    "http://169.254.170.2/",
    "http://192.0.0.192/",
    "http://172.31.255.255/",
    "http://192.168.1.1/",
    "http://[::1]/",
    "http://[::ffff:127.0.0.1]/",
    "http://[fc00::1]/",
    "http://[fe80::1]/",
    "http://[2001:db8::1]/",
    "http://[3fff::1]/",
  ])("rejects non-public literal target %s", (target) => {
    expect(() => assertPublicHttpUrl(target)).toThrow(/not public/);
  });

  it("recognizes global IPv4 and IPv6 addresses", () => {
    expect(isGlobalUnicastIp("8.8.8.8")).toBe(true);
    expect(isGlobalUnicastIp("2606:4700:4700::1111")).toBe(true);
    expect(isGlobalUnicastIp("169.254.169.254")).toBe(false);
    expect(isGlobalUnicastIp("::ffff:10.0.0.1")).toBe(false);
  });

  it("rejects a hostname when any DNS answer is non-public", async () => {
    const privateResolver: OutboundHostResolver = async () => [
      { address: "93.184.216.34", family: 4 },
      { address: "10.0.0.8", family: 4 },
    ];

    await expect(assertSafeOutboundUrl("https://example.com/", {
      resolveHostname: privateResolver,
    })).rejects.toThrow("resolved to a non-public address");
  });

  it("always rejects globally numbered cloud platform endpoints", async () => {
    await expect(assertSafeOutboundUrl("https://mcp.example/", {
      resolveHostname: async () => [{ address: "168.63.129.16", family: 4 }],
    })).rejects.toThrow("non-public address");
    expect(() => assertPublicHttpUrl("http://instance-data.ec2.internal/")).toThrow("not public");
    expect(() => assertPublicHttpUrl("http://foo.metadata.google.internal/")).toThrow("not public");
  });

  it("fails closed on empty or failed DNS results", async () => {
    await expect(assertSafeOutboundUrl("https://example.com/", {
      resolveHostname: async () => [],
    })).rejects.toThrow("did not resolve");
    await expect(assertSafeOutboundUrl("https://example.com/", {
      resolveHostname: async () => {
        throw new Error("DNS unavailable");
      },
    })).rejects.toThrow("could not be resolved");
  });

  it("pins socket lookup to the already-validated DNS answers", async () => {
    const pinned = new Map([
      ["example.com", [
        { address: "93.184.216.34", family: 4 },
        { address: "2606:4700:4700::1111", family: 6 },
      ]],
    ]);
    const socketLookup = createPinnedLookup((hostname) => pinned.get(hostname));

    const answers = await new Promise<unknown>((resolve, reject) => {
      socketLookup("example.com", { all: true }, (error, address) => {
        if (error) reject(error);
        else resolve(address);
      });
    });

    expect(answers).toEqual([
      { address: "93.184.216.34", family: 4 },
      { address: "2606:4700:4700::1111", family: 6 },
    ]);
    await expect(new Promise((resolve, reject) => {
      socketLookup("unapproved.example", {}, (error, address) => {
        if (error) reject(error);
        else resolve(address);
      });
    })).rejects.toThrow("no approved address");
  });

  it("passes the pinned dispatcher to the actual fetch implementation", async () => {
    let dispatcher: unknown;
    const baseFetch: OutboundFetch = async (_url, init) => {
      dispatcher = init?.dispatcher;
      return new Response("ok", { status: 200 });
    };
    const safeFetch = createSafeOutboundFetch({
      fetch: baseFetch,
      resolveHostname: publicResolver,
    });

    await safeFetch("https://example.com/");

    expect(dispatcher).toBeDefined();
  });

  it("enforces response content types and declared response size", async () => {
    const wrongType = createSafeOutboundFetch({
      fetch: async () => new Response("not json", {
        headers: { "content-type": "text/plain" },
      }),
      resolveHostname: publicResolver,
      allowedResponseContentTypes: ["application/json", "application/*+json"],
    });
    await expect(wrongType("https://example.com/")).rejects.toThrow("content type");

    const oversized = createSafeOutboundFetch({
      fetch: async () => new Response(null, {
        headers: { "content-length": "100" },
      }),
      resolveHostname: publicResolver,
      maxResponseBytes: 10,
    });
    await expect(oversized("https://example.com/")).rejects.toThrow("exceeded 10 bytes");
  });

  it("keeps the request deadline while cancelling a rejected response body", async () => {
    const hangingBody = new ReadableStream<Uint8Array>({
      cancel: async () => await new Promise(() => undefined),
    });
    const safeFetch = createSafeOutboundFetch({
      fetch: async () => new Response(hangingBody, {
        headers: { "content-length": "100" },
      }),
      resolveHostname: publicResolver,
      maxResponseBytes: 10,
      requestTimeoutMs: 20,
    });
    const startedAt = Date.now();

    await expect(safeFetch("https://example.com/"))
      .rejects.toThrow("exceeded 20ms");
    expect(Date.now() - startedAt).toBeLessThan(1_000);
  });

  it("stops a chunked response when its body crosses the byte limit", async () => {
    const baseFetch: OutboundFetch = async () => new Response(new ReadableStream({
      start(controller) {
        controller.enqueue(new Uint8Array([1, 2, 3]));
        controller.enqueue(new Uint8Array([4, 5, 6]));
        controller.close();
      },
    }));
    const safeFetch = createSafeOutboundFetch({
      fetch: baseFetch,
      resolveHostname: publicResolver,
      maxResponseBytes: 5,
    });

    const response = await safeFetch("https://example.com/");
    await expect(response.arrayBuffer()).rejects.toThrow("exceeded 5 bytes");
  });

  it("keeps the request deadline while a response body read hangs", async () => {
    const hangingBody = new ReadableStream<Uint8Array>({
      pull: async () => await new Promise(() => undefined),
    });
    const safeFetch = createSafeOutboundFetch({
      fetch: async () => new Response(hangingBody),
      resolveHostname: publicResolver,
      requestTimeoutMs: 20,
    });
    const response = await safeFetch("https://example.com/");
    const startedAt = Date.now();

    await expect(response.arrayBuffer()).rejects.toThrow("exceeded 20ms");
    expect(Date.now() - startedAt).toBeLessThan(1_000);
  });

  it("applies a total request deadline to the fetch and response body", async () => {
    const baseFetch: OutboundFetch = async (_url, init) => await new Promise((_resolve, reject) => {
      const signal = init?.signal;
      const abort = (): void => reject(signal?.reason ?? new Error("aborted"));
      if (signal?.aborted) abort();
      else signal?.addEventListener("abort", abort, { once: true });
    });
    const safeFetch = createSafeOutboundFetch({
      fetch: baseFetch,
      resolveHostname: publicResolver,
      requestTimeoutMs: 10,
    });

    await expect(safeFetch("https://example.com/")).rejects.toThrow("exceeded 10ms");
  });

  it("applies the caller AbortSignal while hostname resolution is pending", async () => {
    const controller = new AbortController();
    const safeFetch = createSafeOutboundFetch({
      fetch: async () => new Response("unreachable"),
      resolveHostname: async () => await new Promise(() => undefined),
      resolveTimeoutMs: 60_000,
      requestTimeoutMs: 60_000,
    });

    const request = safeFetch("https://example.com/", { signal: controller.signal });
    controller.abort(new Error("caller stopped outbound request"));

    await expect(request).rejects.toThrow("caller stopped outbound request");
  });

  it("keeps one overall deadline while resolving redirect hops", async () => {
    let resolutions = 0;
    const resolver: OutboundHostResolver = async () => {
      resolutions += 1;
      if (resolutions === 1) return [{ address: "93.184.216.34", family: 4 }];
      return await new Promise(() => undefined);
    };
    const safeFetch = createSafeOutboundFetch({
      fetch: async () => new Response(null, {
        status: 302,
        headers: { location: "/after-redirect" },
      }),
      resolveHostname: resolver,
      resolveTimeoutMs: 60_000,
      requestTimeoutMs: 20,
    });

    await expect(safeFetch("https://example.com/before-redirect"))
      .rejects.toThrow("exceeded 20ms");
    expect(resolutions).toBe(2);
  });

  it("blocks redirect escapes before making a request to the private target", async () => {
    const baseFetch = vi.fn<OutboundFetch>(async () => new Response(null, {
      status: 302,
      headers: { location: "http://169.254.169.254/latest/meta-data/" },
    }));
    const safeFetch = createSafeOutboundFetch({
      fetch: baseFetch,
      resolveHostname: publicResolver,
    });

    await expect(safeFetch("https://example.com/start")).rejects.toThrow("not public");
    expect(baseFetch).toHaveBeenCalledTimes(1);
  });

  it("cancels a redirect body when the target fails URL policy", async () => {
    let cancellations = 0;
    const redirectBody = new ReadableStream<Uint8Array>({
      cancel() {
        cancellations += 1;
      },
    });
    const safeFetch = createSafeOutboundFetch({
      fetch: async () => new Response(redirectBody, {
        status: 302,
        headers: { location: "http://127.0.0.1/private" },
      }),
      resolveHostname: publicResolver,
    });

    await expect(safeFetch("https://example.com/start")).rejects.toThrow("not public");
    expect(cancellations).toBe(1);
  });

  it("rejects cross-origin redirects so arbitrary authentication headers cannot leak", async () => {
    const baseFetch = vi.fn<OutboundFetch>(async () => new Response(null, {
      status: 302,
      headers: { location: "https://other.example/result" },
    }));
    const safeFetch = createSafeOutboundFetch({
      fetch: baseFetch,
      resolveHostname: publicResolver,
    });

    await expect(safeFetch("https://first.example/start", {
      headers: { "X-Api-Key": "secret" },
    })).rejects.toThrow("cross-origin redirect");
    expect(baseFetch).toHaveBeenCalledTimes(1);
  });

  it("validates and follows same-origin redirect hops", async () => {
    const seen: Array<{ url: string; headers: Headers }> = [];
    const baseFetch: OutboundFetch = async (url, init) => {
      seen.push({ url: url.toString(), headers: new Headers(init?.headers) });
      if (seen.length === 1) {
        return new Response(null, {
          status: 302,
          headers: { location: "/result" },
        });
      }
      return new Response("ok", { status: 200 });
    };
    const safeFetch = createSafeOutboundFetch({
      fetch: baseFetch,
      resolveHostname: publicResolver,
    });

    const response = await safeFetch("https://first.example/start", {
      headers: {
        Authorization: "Bearer secret",
        Cookie: "session=secret",
        "X-Safe": "keep",
      },
    });

    expect(await response.text()).toBe("ok");
    expect(seen.map(({ url }) => url)).toEqual([
      "https://first.example/start",
      "https://first.example/result",
    ]);
    expect(seen[1]!.headers.get("authorization")).toBe("Bearer secret");
    expect(seen[1]!.headers.get("cookie")).toBe("session=secret");
    expect(seen[1]!.headers.get("x-safe")).toBe("keep");
  });

  it("re-resolves a hostname before every outbound request", async () => {
    let resolution = 0;
    const resolver: OutboundHostResolver = async () => {
      resolution += 1;
      return resolution === 1
        ? [{ address: "93.184.216.34", family: 4 }]
        : [{ address: "127.0.0.1", family: 4 }];
    };
    const baseFetch = vi.fn<OutboundFetch>(async () => new Response(null, {
      status: 302,
      headers: { location: "/next" },
    }));
    const safeFetch = createSafeOutboundFetch({ fetch: baseFetch, resolveHostname: resolver });

    await expect(safeFetch("https://example.com/")).rejects.toThrow("non-public address");
    expect(baseFetch).toHaveBeenCalledTimes(1);
  });
});
