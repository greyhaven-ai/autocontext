import { mkdirSync, mkdtempSync, writeFileSync } from "node:fs";
import { tmpdir } from "node:os";
import { join } from "node:path";
import { describe, expect, it } from "vitest";

import {
  discoverAutoctxAgents,
  invokeAutoctxAgent,
  loadAutoctxAgent,
} from "../src/agent-runtime/index.js";
import type { AgentOutput, AgentRuntime } from "../src/runtimes/base.js";
import { createInMemoryWorkspaceEnv } from "../src/runtimes/workspace-env.js";
import { RuntimeSessionEventType } from "../src/session/runtime-events.js";

class FakeRuntime implements AgentRuntime {
  readonly name = "fake-runtime";
  readonly prompts: string[] = [];

  async generate(opts: { prompt: string }): Promise<AgentOutput> {
    this.prompts.push(opts.prompt);
    return { text: `triaged:${opts.prompt}` };
  }

  async revise(): Promise<AgentOutput> {
    throw new Error("not used");
  }
}

describe("experimental agent runtime surface", () => {
  it("discovers handlers only from .autoctx/agents", async () => {
    const root = mkdtempSync(join(tmpdir(), "autoctx-agents-"));
    mkdirSync(join(root, ".autoctx", "agents"), { recursive: true });
    mkdirSync(join(root, ".autoctx", "skills"), { recursive: true });
    mkdirSync(join(root, "scenarios"), { recursive: true });
    writeFileSync(join(root, ".autoctx", "agents", "support.ts"), "export default () => null;\n");
    writeFileSync(join(root, ".autoctx", "agents", "admin.mjs"), "export default () => null;\n");
    writeFileSync(join(root, ".autoctx", "agents", ".hidden.ts"), "export default () => null;\n");
    writeFileSync(join(root, ".autoctx", "agents", "README.md"), "# ignored\n");
    writeFileSync(join(root, ".autoctx", "skills", "skill.ts"), "export default () => null;\n");
    writeFileSync(join(root, "scenarios", "scenario.ts"), "export default () => null;\n");

    const agents = await discoverAutoctxAgents({ cwd: root });

    expect(agents.map((agent) => agent.name)).toEqual(["admin", "support"]);
    expect(agents.map((agent) => agent.relativePath)).toEqual([
      ".autoctx/agents/admin.mjs",
      ".autoctx/agents/support.ts",
    ]);
  });

  it("loads and invokes a typed .autoctx/agents handler through a runtime session", async () => {
    const root = join(import.meta.dirname, "fixtures", "autoctx-agent-project");
    const [entry] = await discoverAutoctxAgents({ cwd: root });
    const loaded = await loadAutoctxAgent(entry!);
    const runtime = new FakeRuntime();
    const workspace = createInMemoryWorkspaceEnv({ cwd: "/repo" });

    const result = await invokeAutoctxAgent(loaded, {
      payload: {
        threadId: "ticket-123",
        message: "please triage",
      },
      env: {
        SUPPORT_TOKEN: "secret-token",
      },
      runtime,
      workspace,
    });

    expect(loaded.name).toBe("support");
    expect(loaded.triggers).toEqual({ webhook: true });
    expect(runtime.prompts).toEqual(["please triage"]);
    expect(result).toMatchObject({
      sessionId: "agent:support:ticket-123",
      role: "support-triager",
      text: "triaged:please triage",
      isError: false,
    });
    expect(result.sessionLog.events.map((event) => event.eventType)).toEqual([
      RuntimeSessionEventType.PROMPT_SUBMITTED,
      RuntimeSessionEventType.ASSISTANT_MESSAGE,
    ]);
    expect(result.sessionLog.events[1]!.payload.metadata).toMatchObject({
      runtime: "fake-runtime",
      runtimeSessionId: "agent:support:ticket-123",
      experimentalAgentRuntime: true,
    });
    expect(JSON.stringify(result.sessionLog.toJSON())).not.toContain("secret-token");
  });
});
