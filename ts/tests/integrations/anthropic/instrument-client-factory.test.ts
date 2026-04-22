/**
 * instrument-client-factory.test.ts — Tests for instrumentClient factory behavior.
 * 3 tests: double-wrap, missing appId, basic wrapping.
 */
import { describe, test, expect } from "vitest";
import Anthropic from "@anthropic-ai/sdk";
import { instrumentClient } from "../../../src/integrations/anthropic/wrap.js";
import { FileSink } from "../../../src/integrations/_shared/sink.js";
import { mkdtempSync, rmSync } from "node:fs";
import { join } from "node:path";
import { tmpdir } from "node:os";

function makeSink() {
  const dir = mkdtempSync(join(tmpdir(), "autoctx-anthropic-factory-"));
  const path = join(dir, "traces.jsonl");
  const sink = new FileSink(path);
  return {
    sink,
    cleanup: () => {
      sink.close();
      rmSync(dir, { recursive: true, force: true });
    },
  };
}

describe("instrumentClient factory", () => {
  test("double-wrap throws 'already wrapped'", () => {
    const { sink, cleanup } = makeSink();
    const inner = new Anthropic({
      apiKey: "test-key",
      fetch: () => Promise.resolve(new Response()),
    });
    const wrapped = instrumentClient(inner, { sink, appId: "my-app" });
    expect(() =>
      instrumentClient(wrapped as unknown as Anthropic, { sink, appId: "my-app" }),
    ).toThrow(/already wrapped/i);
    cleanup();
  });

  test("missing appId throws with helpful message", () => {
    const { sink, cleanup } = makeSink();
    delete process.env["AUTOCONTEXT_APP_ID"];
    const inner = new Anthropic({
      apiKey: "test-key",
      fetch: () => Promise.resolve(new Response()),
    });
    expect(() => instrumentClient(inner, { sink })).toThrow(/app_id/i);
    cleanup();
  });

  test("wraps client successfully with appId", () => {
    const { sink, cleanup } = makeSink();
    const inner = new Anthropic({
      apiKey: "test-key",
      fetch: () => Promise.resolve(new Response()),
    });
    const wrapped = instrumentClient(inner, { sink, appId: "test-app" });
    expect(
      (wrapped as unknown as Record<symbol, boolean>)[Symbol.for("autocontext.wrapped")],
    ).toBe(true);
    // Passthrough of non-intercepted properties
    expect((wrapped as unknown as Record<string, unknown>).apiKey).toBe("test-key");
    cleanup();
  });
});
