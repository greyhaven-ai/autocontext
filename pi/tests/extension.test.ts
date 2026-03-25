/**
 * Tests for AC-427: Official Pi package/extension for autocontext.
 *
 * Validates:
 * - Extension entry point registers expected tools
 * - Tool handlers execute correctly with mock Pi API
 * - Package manifest has correct Pi configuration
 * - SKILL.md has valid frontmatter
 */

import { describe, it, expect, beforeEach } from "vitest";
import { readFileSync, existsSync } from "node:fs";
import { join } from "node:path";

// ---------------------------------------------------------------------------
// Mock Pi ExtensionAPI
// ---------------------------------------------------------------------------

interface RegisteredTool {
  name: string;
  label: string;
  description: string;
  parameters: unknown;
  execute: (...args: unknown[]) => Promise<unknown>;
}

interface RegisteredCommand {
  name: string;
  handler: (...args: unknown[]) => Promise<unknown>;
}

function createMockPiAPI() {
  const tools: RegisteredTool[] = [];
  const commands: RegisteredCommand[] = [];
  const events: Map<string, Array<(...args: unknown[]) => void>> = new Map();

  return {
    tools,
    commands,
    events,

    registerTool(def: RegisteredTool) {
      tools.push(def);
    },

    registerCommand(name: string, opts: { handler: (...args: unknown[]) => Promise<unknown> }) {
      commands.push({ name, handler: opts.handler });
    },

    on(event: string, handler: (...args: unknown[]) => void) {
      const handlers = events.get(event) ?? [];
      handlers.push(handler);
      events.set(event, handlers);
    },
  };
}

// ---------------------------------------------------------------------------
// Package manifest
// ---------------------------------------------------------------------------

describe("Package manifest", () => {
  const pkgPath = join(import.meta.dirname, "..", "package.json");

  it("has pi-package keyword", () => {
    const pkg = JSON.parse(readFileSync(pkgPath, "utf-8"));
    expect(pkg.keywords).toContain("pi-package");
  });

  it("has pi.extensions pointing to entry point", () => {
    const pkg = JSON.parse(readFileSync(pkgPath, "utf-8"));
    expect(pkg.pi).toBeDefined();
    expect(pkg.pi.extensions).toContain("./src/index.ts");
  });

  it("has pi.skills pointing to skills dir", () => {
    const pkg = JSON.parse(readFileSync(pkgPath, "utf-8"));
    expect(pkg.pi.skills).toContain("./skills");
  });

  it("lists Pi core packages as peerDependencies", () => {
    const pkg = JSON.parse(readFileSync(pkgPath, "utf-8"));
    expect(pkg.peerDependencies["@mariozechner/pi-coding-agent"]).toBe("*");
    expect(pkg.peerDependencies["@mariozechner/pi-ai"]).toBe("*");
  });
});

// ---------------------------------------------------------------------------
// SKILL.md
// ---------------------------------------------------------------------------

describe("SKILL.md", () => {
  const skillPath = join(import.meta.dirname, "..", "skills", "autocontext", "SKILL.md");

  it("exists at skills/autocontext/SKILL.md", () => {
    expect(existsSync(skillPath)).toBe(true);
  });

  it("has valid frontmatter with required fields", () => {
    const content = readFileSync(skillPath, "utf-8");
    expect(content).toMatch(/^---\n/);
    expect(content).toMatch(/name:\s*autocontext/);
    expect(content).toMatch(/description:/);
  });

  it("skill name matches directory name", () => {
    const content = readFileSync(skillPath, "utf-8");
    const nameMatch = content.match(/name:\s*(\S+)/);
    expect(nameMatch).not.toBeNull();
    expect(nameMatch![1]).toBe("autocontext");
  });
});

// ---------------------------------------------------------------------------
// Prompt templates
// ---------------------------------------------------------------------------

describe("Prompt templates", () => {
  const promptsDir = join(import.meta.dirname, "..", "prompts");

  it("has a status prompt template", () => {
    expect(existsSync(join(promptsDir, "autoctx-status.md"))).toBe(true);
  });

  it("status prompt references autoctx tools", () => {
    const content = readFileSync(join(promptsDir, "autoctx-status.md"), "utf-8");
    expect(content).toContain("autocontext");
  });
});

// ---------------------------------------------------------------------------
// Extension entry point
// ---------------------------------------------------------------------------

describe("Extension entry point", () => {
  it("exports a default function", async () => {
    const mod = await import("../src/index.js");
    expect(typeof mod.default).toBe("function");
  });

  it("registers autocontext tools when called", async () => {
    const mod = await import("../src/index.js");
    const api = createMockPiAPI();
    mod.default(api as unknown);
    expect(api.tools.length).toBeGreaterThanOrEqual(4);
  });

  it("registers autocontext_judge tool", async () => {
    const mod = await import("../src/index.js");
    const api = createMockPiAPI();
    mod.default(api as unknown);
    const judge = api.tools.find((t) => t.name === "autocontext_judge");
    expect(judge).toBeDefined();
    expect(judge!.description.toLowerCase()).toContain("evaluat");
  });

  it("registers autocontext_improve tool", async () => {
    const mod = await import("../src/index.js");
    const api = createMockPiAPI();
    mod.default(api as unknown);
    const improve = api.tools.find((t) => t.name === "autocontext_improve");
    expect(improve).toBeDefined();
  });

  it("registers autocontext_status tool", async () => {
    const mod = await import("../src/index.js");
    const api = createMockPiAPI();
    mod.default(api as unknown);
    const status = api.tools.find((t) => t.name === "autocontext_status");
    expect(status).toBeDefined();
  });

  it("registers autocontext_scenarios tool", async () => {
    const mod = await import("../src/index.js");
    const api = createMockPiAPI();
    mod.default(api as unknown);
    const scenarios = api.tools.find((t) => t.name === "autocontext_scenarios");
    expect(scenarios).toBeDefined();
  });

  it("registers autocontext_queue tool", async () => {
    const mod = await import("../src/index.js");
    const api = createMockPiAPI();
    mod.default(api as unknown);
    const queue = api.tools.find((t) => t.name === "autocontext_queue");
    expect(queue).toBeDefined();
  });

  it("registers /autocontext slash command", async () => {
    const mod = await import("../src/index.js");
    const api = createMockPiAPI();
    mod.default(api as unknown);
    const cmd = api.commands.find((c) => c.name === "autocontext");
    expect(cmd).toBeDefined();
  });

  it("subscribes to session_start event", async () => {
    const mod = await import("../src/index.js");
    const api = createMockPiAPI();
    mod.default(api as unknown);
    expect(api.events.has("session_start")).toBe(true);
  });
});

// ---------------------------------------------------------------------------
// Tool parameter schemas
// ---------------------------------------------------------------------------

describe("Tool parameter schemas", () => {
  it("autocontext_judge has task_prompt, agent_output, rubric params", async () => {
    const mod = await import("../src/index.js");
    const api = createMockPiAPI();
    mod.default(api as unknown);
    const judge = api.tools.find((t) => t.name === "autocontext_judge")!;
    const schema = judge.parameters as Record<string, unknown>;
    const props = (schema as { properties?: Record<string, unknown> }).properties;
    expect(props).toBeDefined();
    expect(props!.task_prompt).toBeDefined();
    expect(props!.agent_output).toBeDefined();
    expect(props!.rubric).toBeDefined();
  });

  it("autocontext_improve has task_prompt, initial_output, rubric params", async () => {
    const mod = await import("../src/index.js");
    const api = createMockPiAPI();
    mod.default(api as unknown);
    const improve = api.tools.find((t) => t.name === "autocontext_improve")!;
    const schema = improve.parameters as Record<string, unknown>;
    const props = (schema as { properties?: Record<string, unknown> }).properties;
    expect(props).toBeDefined();
    expect(props!.task_prompt).toBeDefined();
    expect(props!.initial_output).toBeDefined();
    expect(props!.rubric).toBeDefined();
  });

  it("autocontext_queue has spec_name param", async () => {
    const mod = await import("../src/index.js");
    const api = createMockPiAPI();
    mod.default(api as unknown);
    const queue = api.tools.find((t) => t.name === "autocontext_queue")!;
    const schema = queue.parameters as Record<string, unknown>;
    const props = (schema as { properties?: Record<string, unknown> }).properties;
    expect(props).toBeDefined();
    expect(props!.spec_name).toBeDefined();
  });
});
