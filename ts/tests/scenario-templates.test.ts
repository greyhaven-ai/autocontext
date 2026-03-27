/**
 * AC-443: Scenario templates — pre-built patterns without LLM generation.
 */

import { describe, it, expect, beforeEach, afterEach } from "vitest";
import { mkdtempSync, rmSync, existsSync, readFileSync } from "node:fs";
import { join } from "node:path";
import { tmpdir } from "node:os";
import { spawnSync } from "node:child_process";
import {
  TemplateLoader,
  type TemplateSpec,
} from "../src/scenarios/templates/index.js";

let loader: TemplateLoader;

beforeEach(() => {
  loader = new TemplateLoader();
});

// ---------------------------------------------------------------------------
// Template discovery
// ---------------------------------------------------------------------------

describe("template listing", () => {
  it("lists at least 3 built-in templates", () => {
    const templates = loader.listTemplates();
    expect(templates.length).toBeGreaterThanOrEqual(3);
  });

  it("includes content-generation template", () => {
    const templates = loader.listTemplates();
    const names = templates.map((t) => t.name);
    expect(names).toContain("content-generation");
  });

  it("includes prompt-optimization template", () => {
    const templates = loader.listTemplates();
    const names = templates.map((t) => t.name);
    expect(names).toContain("prompt-optimization");
  });

  it("includes rag-accuracy template", () => {
    const templates = loader.listTemplates();
    const names = templates.map((t) => t.name);
    expect(names).toContain("rag-accuracy");
  });

  it("all templates have required fields", () => {
    const templates = loader.listTemplates();
    for (const t of templates) {
      expect(t.name).toBeTruthy();
      expect(t.description).toBeTruthy();
      expect(t.taskPrompt).toBeTruthy();
      expect(t.judgeRubric).toBeTruthy();
      expect(t.outputFormat).toBeTruthy();
      expect(t.maxRounds).toBeGreaterThanOrEqual(1);
      expect(t.qualityThreshold).toBeGreaterThan(0);
      expect(t.qualityThreshold).toBeLessThanOrEqual(1);
    }
  });
});

// ---------------------------------------------------------------------------
// Template retrieval
// ---------------------------------------------------------------------------

describe("template retrieval", () => {
  it("gets a specific template by name", () => {
    const template = loader.getTemplate("content-generation");
    expect(template.name).toBe("content-generation");
    expect(template.taskPrompt).toContain("blog");
  });

  it("throws for unknown template", () => {
    expect(() => loader.getTemplate("nonexistent")).toThrow();
  });

  it("content-generation has rubric dimensions", () => {
    const template = loader.getTemplate("content-generation");
    expect(template.rubricDimensions).toBeDefined();
    expect(template.rubricDimensions!.length).toBeGreaterThanOrEqual(3);
  });
});

// ---------------------------------------------------------------------------
// Template scaffolding
// ---------------------------------------------------------------------------

describe("template scaffolding", () => {
  let tmpDir: string;

  beforeEach(() => {
    tmpDir = mkdtempSync(join(tmpdir(), "ac-443-test-"));
  });
  afterEach(() => {
    rmSync(tmpDir, { recursive: true, force: true });
  });

  it("scaffolds a template to a target directory", () => {
    const targetDir = join(tmpDir, "my_scenario");
    loader.scaffold("content-generation", targetDir);

    expect(existsSync(join(targetDir, "spec.json"))).toBe(true);
    expect(existsSync(join(targetDir, "scenario_type.txt"))).toBe(true);
  });

  it("scaffolded spec.json is valid and contains template data", () => {
    const targetDir = join(tmpDir, "scaffolded");
    loader.scaffold("prompt-optimization", targetDir);

    const spec = JSON.parse(readFileSync(join(targetDir, "spec.json"), "utf-8"));
    expect(spec.name).toBe("prompt-optimization");
    expect(spec.taskPrompt).toBeTruthy();
    expect(spec.judgeRubric).toBeTruthy();
  });

  it("scaffolded scenario_type.txt contains agent_task marker", () => {
    const targetDir = join(tmpDir, "typed");
    loader.scaffold("rag-accuracy", targetDir);

    const marker = readFileSync(join(targetDir, "scenario_type.txt"), "utf-8").trim();
    expect(marker).toBe("agent_task");
  });

  it("scaffolded agent_task_spec.json is loadable by custom-loader", () => {
    const targetDir = join(tmpDir, "loadable");
    loader.scaffold("content-generation", targetDir);

    expect(existsSync(join(targetDir, "agent_task_spec.json"))).toBe(true);
    const atSpec = JSON.parse(readFileSync(join(targetDir, "agent_task_spec.json"), "utf-8"));
    expect(atSpec.task_prompt).toBeTruthy();
    expect(atSpec.judge_rubric).toBeTruthy();
  });

  it("scaffold applies overrides", () => {
    const targetDir = join(tmpDir, "overridden");
    loader.scaffold("content-generation", targetDir, { maxRounds: 5 });

    const spec = JSON.parse(readFileSync(join(targetDir, "spec.json"), "utf-8"));
    expect(spec.maxRounds).toBe(5);
  });

  it("scaffold can override the scenario name for persisted artifacts", () => {
    const targetDir = join(tmpDir, "named-task");
    loader.scaffold("prompt-optimization", targetDir, { name: "named-task" });

    const spec = JSON.parse(readFileSync(join(targetDir, "spec.json"), "utf-8"));
    const agentTaskSpec = JSON.parse(readFileSync(join(targetDir, "agent_task_spec.json"), "utf-8"));
    expect(spec.name).toBe("named-task");
    expect(agentTaskSpec.name).toBe("named-task");
  });
});

// ---------------------------------------------------------------------------
// TemplateSpec shape
// ---------------------------------------------------------------------------

describe("TemplateSpec", () => {
  it("has all expected fields", () => {
    const t: TemplateSpec = loader.getTemplate("content-generation");
    expect(typeof t.name).toBe("string");
    expect(typeof t.description).toBe("string");
    expect(typeof t.taskPrompt).toBe("string");
    expect(typeof t.judgeRubric).toBe("string");
    expect(typeof t.outputFormat).toBe("string");
    expect(typeof t.maxRounds).toBe("number");
    expect(typeof t.qualityThreshold).toBe("number");
  });
});

describe("template CLI integration", () => {
  let tmpDir: string;
  const CLI = join(import.meta.dirname, "..", "src", "cli", "index.ts");

  beforeEach(() => {
    tmpDir = mkdtempSync(join(tmpdir(), "ac-443-cli-"));
  });

  afterEach(() => {
    rmSync(tmpDir, { recursive: true, force: true });
  });

  it("lists built-in templates through the real CLI", () => {
    const result = spawnSync("npx", ["tsx", CLI, "new-scenario", "--list"], {
      cwd: tmpDir,
      encoding: "utf-8",
    });

    expect(result.status).toBe(0);
    expect(result.stdout).toContain("content-generation");
    expect(result.stdout).toContain("prompt-optimization");
    expect(result.stdout).toContain("rag-accuracy");
  });

  it("scaffolds a template through the real CLI into knowledge/_custom_scenarios", () => {
    const result = spawnSync("npx", ["tsx", CLI, "new-scenario", "--template", "prompt-optimization", "--name", "my-prompt-task", "--json"], {
      cwd: tmpDir,
      encoding: "utf-8",
    });

    expect(result.status).toBe(0);
    const payload = JSON.parse(result.stdout);
    const targetDir = join(tmpDir, "knowledge", "_custom_scenarios", "my-prompt-task");
    expect(payload.name).toBe("my-prompt-task");
    expect(payload.template).toBe("prompt-optimization");
    expect(payload.path).toContain("/knowledge/_custom_scenarios/my-prompt-task");
    expect(existsSync(join(targetDir, "spec.json"))).toBe(true);
    expect(existsSync(join(targetDir, "agent_task_spec.json"))).toBe(true);
    expect(existsSync(join(targetDir, "scenario_type.txt"))).toBe(true);

    const spec = JSON.parse(readFileSync(join(targetDir, "spec.json"), "utf-8"));
    expect(spec.name).toBe("my-prompt-task");
  });
});
