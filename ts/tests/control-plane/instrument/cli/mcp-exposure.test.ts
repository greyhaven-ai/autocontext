/**
 * A2-I Layer 9 — MCP tool privacy assertion (Layer 6+7 concern #3).
 *
 * Ensures the `instrument` MCP tool's input schema does NOT accept test-only
 * private fields (`branchExecutor`, `enhancementProvider`, `gitDetector`,
 * `registeredPluginsOverride`, `skipSessionDirWrite`). These are private-by-
 * convention on `InstrumentInputs` but that convention is easy to break
 * silently if someone wires the MCP surface to accept arbitrary runtime
 * injection. This test catches that class of mistake.
 *
 * The MCP tool registers via `registerInstrumentTools(server)` passing a
 * Zod schema as its third argument. We spy on the call to inspect the
 * declared schema shape without booting an actual MCP server.
 */
import { describe, test, expect } from "vitest";
import { registerInstrumentTools } from "../../../../src/mcp/instrument-tools.js";

interface ToolCall {
  name: string;
  description: string;
  schema: Record<string, unknown>;
}

describe("MCP instrument tool — private-field exposure check", () => {
  test("input schema rejects private test-only fields", () => {
    const captured: ToolCall[] = [];
    const spyServer = {
      tool: (name: string, description: string, schema: Record<string, unknown>) => {
        captured.push({ name, description, schema });
        return { name };
      },
    };
    registerInstrumentTools(spyServer);

    expect(captured.length).toBeGreaterThan(0);
    const instrumentTool = captured.find((t) => t.name === "instrument");
    expect(instrumentTool).toBeDefined();
    const schemaKeys = Object.keys(instrumentTool!.schema);

    // Private test-only fields that MUST NOT be in the MCP input schema:
    const forbiddenFields = [
      "branchExecutor",
      "enhancementProvider",
      "gitDetector",
      "registeredPluginsOverride",
      "skipSessionDirWrite",
    ];
    for (const forbidden of forbiddenFields) {
      expect(
        schemaKeys,
        `MCP instrument tool exposes private field "${forbidden}" — test-only injection points must not be part of the public tool surface`,
      ).not.toContain(forbidden);
    }

    // Also verify the public surface includes the documented CLI flags (sanity check).
    const expectedPublicFields = [
      "cwd",
      "mode",
      "exclude",
      "failIfEmpty",
      "force",
      "enhanced",
    ];
    for (const expected of expectedPublicFields) {
      expect(schemaKeys).toContain(expected);
    }
  });
});
