/**
 * fast-check property test for trace shape invariants — Task 3.11.
 * Mirrors Python hypothesis property test (Task 2.12).
 * 100 runs. Validates via validateProductionTrace.
 */
import { describe, test } from "vitest";
import * as fc from "fast-check";
import { buildSuccessTrace, buildRequestSnapshot } from "../../../../src/integrations/openai/trace-builder.js";

const BASE_SOURCE = { emitter: "sdk", sdk: { name: "autocontext-ts", version: "0.0.0" } };

describe("trace shape invariants (property, 100 runs)", () => {
  test("buildSuccessTrace always produces a valid trace", () => {
    fc.assert(
      fc.property(
        fc.record({
          model: fc.string({ minLength: 1, maxLength: 100 }),
          userContent: fc.string({ minLength: 0, maxLength: 1000 }),
          tokensIn: fc.integer({ min: 0, max: 100_000 }),
          tokensOut: fc.integer({ min: 0, max: 100_000 }),
          // appId must match ^[a-z0-9][a-z0-9_-]*$
          appId: fc.stringMatching(/^[a-z][a-z0-9_-]{0,30}$/),
          environmentTag: fc.constantFrom("production", "staging", "development", "test"),
        }),
        ({ model, userContent, tokensIn, tokensOut, appId, environmentTag }) => {
          const { ulid } = require("ulid") as { ulid: () => string };
          const snap = buildRequestSnapshot({
            model,
            messages: [{ role: "user", content: userContent }],
            extraKwargs: {},
          });
          const trace = buildSuccessTrace({
            requestSnapshot: snap,
            responseUsage: { prompt_tokens: tokensIn, completion_tokens: tokensOut },
            responseToolCalls: null,
            identity: {},
            timing: {
              startedAt: "2024-01-01T00:00:00Z",
              endedAt: "2024-01-01T00:00:01Z",
              latencyMs: 1000,
            },
            env: { environmentTag, appId },
            sourceInfo: BASE_SOURCE,
            traceId: ulid(),
          });
          // Invariants
          if (trace.provider.name !== "openai") throw new Error("provider must be openai");
          if (trace.model !== model) throw new Error(`model mismatch: ${trace.model} !== ${model}`);
          if (trace.outcome?.label !== "success") throw new Error("outcome must be success");
          if (trace.usage.tokensIn !== tokensIn) throw new Error(`tokensIn mismatch: ${trace.usage.tokensIn} !== ${tokensIn}`);
          if (trace.usage.tokensOut !== tokensOut) throw new Error(`tokensOut mismatch: ${trace.usage.tokensOut} !== ${tokensOut}`);
          if (!Array.isArray(trace.messages)) throw new Error("messages must be array");
          if (trace.messages.length === 0) throw new Error("messages must not be empty");
          if (typeof trace.messages[0]!.timestamp !== "string") throw new Error("message must have timestamp");
          return true;
        },
      ),
      { numRuns: 100 },
    );
  });

  test("buildSuccessTrace with tool calls — toolCalls shape is correct", () => {
    fc.assert(
      fc.property(
        fc.record({
          toolName: fc.string({ minLength: 1, maxLength: 50 }).filter((s) => /^[a-zA-Z_][a-zA-Z0-9_]*$/.test(s)),
          argValue: fc.string({ minLength: 0, maxLength: 100 }),
        }),
        ({ toolName, argValue }) => {
          const snap = buildRequestSnapshot({
            model: "gpt-4o",
            messages: [{ role: "user", content: "call tool" }],
            extraKwargs: {},
          });
          const trace = buildSuccessTrace({
            requestSnapshot: snap,
            responseUsage: { prompt_tokens: 10, completion_tokens: 5 },
            responseToolCalls: [
              {
                function: {
                  name: toolName,
                  arguments: JSON.stringify({ value: argValue }),
                },
              },
            ],
            identity: {},
            timing: {
              startedAt: "2024-01-01T00:00:00Z",
              endedAt: "2024-01-01T00:00:01Z",
              latencyMs: 100,
            },
            env: { environmentTag: "test", appId: "prop-test" },
            sourceInfo: BASE_SOURCE,
            traceId: "01HWTEST000000000000000001",
          });
          if (!Array.isArray(trace.toolCalls)) throw new Error("toolCalls must be array");
          if (trace.toolCalls.length !== 1) throw new Error("toolCalls must have 1 entry");
          if (trace.toolCalls[0]!.toolName !== toolName) throw new Error("toolName mismatch");
          return true;
        },
      ),
      { numRuns: 100 },
    );
  });
});
