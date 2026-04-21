#!/usr/bin/env node
// Run with: node --import tsx/esm scripts/drive-parity-fixture.mjs
/**
 * Cross-runtime parity fixture driver — TypeScript runtime.
 *
 * Usage: node scripts/drive-parity-fixture.mjs <fixture-name>
 *
 * Reads fixture inputs, runs instrumentClient with a mock OpenAI client,
 * captures the emitted trace, normalizes non-deterministic fields (traceId,
 * timestamps, latencyMs, SDK version), and prints canonical JSON to stdout.
 *
 * Exit 0 on success, 1 on error.
 */

import { readFileSync, existsSync, mkdtempSync, rmSync } from "node:fs";
import { join, dirname, resolve } from "node:path";
import { fileURLToPath } from "node:url";
import { tmpdir } from "node:os";

const __dirname = dirname(fileURLToPath(import.meta.url));
const ROOT = resolve(__dirname, "..");
const FIXTURES_DIR = join(ROOT, "tests", "integrations", "openai", "parity", "fixtures");

const fixtureName = process.argv[2];
if (!fixtureName) {
  process.stderr.write("Usage: node drive-parity-fixture.mjs <fixture-name>\n");
  process.exit(1);
}

const fixtureDir = join(FIXTURES_DIR, fixtureName);
if (!existsSync(fixtureDir)) {
  process.stderr.write(`Fixture not found: ${fixtureDir}\n`);
  process.exit(1);
}

// Load fixture files
const requestJson = JSON.parse(readFileSync(join(fixtureDir, "request.json"), "utf-8"));
const identityJson = JSON.parse(readFileSync(join(fixtureDir, "identity.json"), "utf-8"));
const isError = existsSync(join(fixtureDir, "error.json"));
const isStreaming = requestJson.stream === true;
const isResponsesApi = ("input" in requestJson || requestJson.endpoint === "responses");

// Set up a temp sink
const tmpDir = mkdtempSync(join(tmpdir(), "parity-driver-"));
const tracePath = join(tmpDir, "traces.jsonl");

try {
  // Dynamic import to get ESM modules from src
  const { FileSink } = await import("../src/integrations/openai/sink.js");
  const { instrumentClient } = await import("../src/integrations/openai/wrap.js");
  const { autocontextSession } = await import("../src/integrations/openai/session.js");

  // Build mock fetch
  let mockFetch;
  if (isError) {
    const errorJson = JSON.parse(readFileSync(join(fixtureDir, "error.json"), "utf-8"));
    mockFetch = (_url, _init) => Promise.resolve(
      new Response(
        JSON.stringify({ error: { message: errorJson.message, type: "api_error", code: null } }),
        { status: errorJson.status, headers: { "content-type": "application/json" } },
      ),
    );
  } else if (isStreaming) {
    const chunks = JSON.parse(readFileSync(join(fixtureDir, "response.json"), "utf-8"));
    mockFetch = (_url, _init) => {
      const lines = chunks.map(c => `data: ${JSON.stringify(c)}\n\n`).join("") + "data: [DONE]\n\n";
      return Promise.resolve(new Response(lines, {
        status: 200,
        headers: { "content-type": "text/event-stream" },
      }));
    };
  } else {
    const responseJson = JSON.parse(readFileSync(join(fixtureDir, "response.json"), "utf-8"));
    mockFetch = (_url, _init) => Promise.resolve(
      new Response(JSON.stringify(responseJson), {
        status: 200,
        headers: { "content-type": "application/json" },
      }),
    );
  }

  // Set up install salt for session fixtures
  const originalDir = process.cwd();
  const saltFile = join(fixtureDir, "install-salt.txt");
  let changedDir = false;
  if (existsSync(saltFile)) {
    // Create a temp dir with .autocontext/install-salt matching the fixture salt
    const { mkdirSync, writeFileSync, mkdtempSync: mkdtempSyncFs } = await import("node:fs");
    const saltTmpDir = mkdtempSyncFs(join(tmpdir(), "parity-salt-"));
    mkdirSync(join(saltTmpDir, ".autocontext"), { recursive: true });
    const saltContent = readFileSync(saltFile, "utf-8").trim();
    writeFileSync(join(saltTmpDir, ".autocontext", "install-salt"), saltContent);
    process.chdir(saltTmpDir);
    changedDir = true;
  }

  // Create OpenAI-like client mock
  const { default: OpenAI } = await import("openai");
  const inner = new OpenAI({ apiKey: "test-key", fetch: mockFetch, maxRetries: 0 });

  const sink = new FileSink(tracePath, { batchSize: 1, flushIntervalSeconds: 0 });
  const client = instrumentClient(inner, {
    sink,
    appId: "parity-test-app",
    environmentTag: "test",
  });

  // Handle session identity
  const runRequest = async () => {
    // Run the request
    if (isResponsesApi) {
      try {
        await client.responses.create(requestJson);
      } catch {
        // expected for error fixtures
      }
    } else if (isStreaming) {
      try {
        if (fixtureName === "chat-streaming-abandoned") {
          // Wrap in a sub-function so stream + iter go out of scope when it returns,
          // making them eligible for GC before we call gc().
          await (async () => {
            const stream = await client.chat.completions.create(requestJson);
            const iter = stream[Symbol.asyncIterator]();
            await iter.next(); // read one chunk, then let everything go out of scope
            // Do NOT reference stream or iter after this point
          })();
          // Now stream and iter are out of scope; force GC if available
          if (typeof gc !== "undefined") {
            gc();
            gc(); // second pass to collect cycles
          }
          // Wait a bit for FinalizationRegistry callbacks to fire
          await new Promise(r => setTimeout(r, 200));
        } else {
          const stream = await client.chat.completions.create(requestJson);
          for await (const _chunk of stream) { /* consume */ }
        }
      } catch {
        // expected for error fixtures
      }
    } else {
      try {
        await client.chat.completions.create(requestJson);
      } catch {
        // expected for error fixtures
      }
    }
    sink.flush();
    sink.close();
  };

  if (identityJson.userId || identityJson.sessionId) {
    await autocontextSession(
      { userId: identityJson.userId, sessionId: identityJson.sessionId },
      runRequest,
    );
  } else {
    await runRequest();
  }

  // Restore original directory if we changed it
  if (changedDir) {
    process.chdir(originalDir);
  }

  // Read the emitted trace
  let rawTrace;
  try {
    const content = readFileSync(tracePath, "utf-8").trim();
    if (!content) {
      process.stderr.write("No trace emitted\n");
      process.exit(1);
    }
    rawTrace = JSON.parse(content.split("\n")[0]);
  } catch (e) {
    process.stderr.write(`Failed to read trace: ${e}\n`);
    process.exit(1);
  }

  // Normalize non-deterministic fields for cross-runtime parity
  const normalized = normalizeTrace(rawTrace, fixtureName);

  // Print canonical JSON (sorted keys, no spaces)
  process.stdout.write(canonicalJson(normalized) + "\n");
  process.exit(0);
} finally {
  rmSync(tmpDir, { recursive: true, force: true });
}

function normalizeTrace(trace, fixtureName) {
  const t = { ...trace };
  // Normalize traceId → deterministic constant
  t.traceId = "PARITY_TRACE_ID_NORMALIZED";
  // Normalize timing
  t.timing = {
    startedAt: "2024-01-01T00:00:00Z",
    endedAt: "2024-01-01T00:00:01Z",
    latencyMs: 1000,
  };
  // Normalize SDK name + version in source (different runtimes have different names)
  if (t.source?.sdk) {
    t.source = { ...t.source, sdk: { name: "autocontext-sdk", version: "0.0.0" } };
  }
  // Normalize message timestamps
  if (Array.isArray(t.messages)) {
    t.messages = t.messages.map(m => ({ ...m, timestamp: "2024-01-01T00:00:00Z" }));
  }
  // Normalize error fields (message format, stack, and error-type vary between SDK versions/runtimes)
  if (t.outcome?.error) {
    const err = { ...t.outcome.error };
    if (err.stack) err.stack = "NORMALIZED";
    if (err.message) err.message = "NORMALIZED";
    if (err.type) err.type = "NORMALIZED";
    t.outcome = { ...t.outcome, error: err };
  }
  return t;
}

function canonicalJson(obj) {
  if (Array.isArray(obj)) return "[" + obj.map(canonicalJson).join(",") + "]";
  if (obj === null) return "null";
  if (typeof obj !== "object") return JSON.stringify(obj);
  const keys = Object.keys(obj).sort();
  return "{" + keys.map(k => JSON.stringify(k) + ":" + canonicalJson(obj[k])).join(",") + "}";
}
