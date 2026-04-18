import { describe, test, expect, beforeEach, afterEach } from "vitest";
import { mkdtempSync, mkdirSync, rmSync, writeFileSync } from "node:fs";
import { tmpdir } from "node:os";
import { join } from "node:path";
import {
  defaultWorkspaceLayout,
  loadWorkspaceLayout,
} from "../../../src/control-plane/emit/workspace-layout.js";

describe("defaultWorkspaceLayout", () => {
  test("has the conventional defaults documented in the spec", () => {
    const layout = defaultWorkspaceLayout();
    expect(layout.promptSubdir).toBe("prompts");
    expect(layout.policySubdir).toBe("policies/tools");
    expect(layout.routingSubdir).toBe("routing");
    expect(layout.modelPointerSubdir).toBe("models/active");
    expect(layout.scenarioDir("grid_ctf", "production")).toBe("agents/grid_ctf");
  });

  test("is stable across calls (same subdirs, equivalent scenarioDir output)", () => {
    const a = defaultWorkspaceLayout();
    const b = defaultWorkspaceLayout();
    expect(a.promptSubdir).toBe(b.promptSubdir);
    expect(a.policySubdir).toBe(b.policySubdir);
    expect(a.routingSubdir).toBe(b.routingSubdir);
    expect(a.modelPointerSubdir).toBe(b.modelPointerSubdir);
    expect(a.scenarioDir("s", "e")).toBe(b.scenarioDir("s", "e"));
  });
});

describe("loadWorkspaceLayout", () => {
  let cwd: string;

  beforeEach(() => {
    cwd = mkdtempSync(join(tmpdir(), "autocontext-workspace-layout-"));
  });

  afterEach(() => {
    rmSync(cwd, { recursive: true, force: true });
  });

  test("returns defaults when no .autocontext/workspace.json exists", () => {
    const layout = loadWorkspaceLayout(cwd);
    const d = defaultWorkspaceLayout();
    expect(layout.promptSubdir).toBe(d.promptSubdir);
    expect(layout.policySubdir).toBe(d.policySubdir);
    expect(layout.routingSubdir).toBe(d.routingSubdir);
    expect(layout.modelPointerSubdir).toBe(d.modelPointerSubdir);
    expect(layout.scenarioDir("s", "e")).toBe(d.scenarioDir("s", "e"));
  });

  test("merges a partial workspace.json on top of defaults per-field", () => {
    mkdirSync(join(cwd, ".autocontext"), { recursive: true });
    writeFileSync(
      join(cwd, ".autocontext", "workspace.json"),
      JSON.stringify({ promptSubdir: "my-prompts" }),
    );
    const layout = loadWorkspaceLayout(cwd);
    expect(layout.promptSubdir).toBe("my-prompts");
    // Every other field retains the default.
    const d = defaultWorkspaceLayout();
    expect(layout.policySubdir).toBe(d.policySubdir);
    expect(layout.routingSubdir).toBe(d.routingSubdir);
    expect(layout.modelPointerSubdir).toBe(d.modelPointerSubdir);
    expect(layout.scenarioDir("s", "e")).toBe(d.scenarioDir("s", "e"));
  });

  test("honours a custom scenarioDir template with ${scenario} and ${env} substitution", () => {
    mkdirSync(join(cwd, ".autocontext"), { recursive: true });
    writeFileSync(
      join(cwd, ".autocontext", "workspace.json"),
      JSON.stringify({ scenarioDirTemplate: "envs/${env}/scenarios/${scenario}" }),
    );
    const layout = loadWorkspaceLayout(cwd);
    expect(layout.scenarioDir("grid_ctf", "staging")).toBe("envs/staging/scenarios/grid_ctf");
  });

  test("silently ignores unknown fields in workspace.json (forward-compat)", () => {
    mkdirSync(join(cwd, ".autocontext"), { recursive: true });
    writeFileSync(
      join(cwd, ".autocontext", "workspace.json"),
      JSON.stringify({ unrelatedFutureField: 42, promptSubdir: "p" }),
    );
    const layout = loadWorkspaceLayout(cwd);
    expect(layout.promptSubdir).toBe("p");
  });

  test("throws on malformed JSON (loud, not silent)", () => {
    mkdirSync(join(cwd, ".autocontext"), { recursive: true });
    writeFileSync(join(cwd, ".autocontext", "workspace.json"), "{not json");
    expect(() => loadWorkspaceLayout(cwd)).toThrow(/workspace\.json/);
  });
});
