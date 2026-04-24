import { describe, expect, it } from "vitest";

import { CAPABILITIES_COMMANDS } from "../src/cli/capabilities-command-workflow.js";
import { getCapabilities } from "../src/mcp/capabilities.js";
import { SUPPORTED_PROVIDER_TYPES } from "../src/providers/provider-factory.js";

describe("capabilities provider parity", () => {
  it("reports the provider factory support surface", () => {
    expect(getCapabilities().providers).toEqual([...SUPPORTED_PROVIDER_TYPES]);
  });

  it("does not mark visible TypeScript commands as Python-only", () => {
    const capabilities = getCapabilities();

    expect(CAPABILITIES_COMMANDS).toContain("train");
    expect(capabilities.pythonOnly).not.toContain("train");
  });
});
