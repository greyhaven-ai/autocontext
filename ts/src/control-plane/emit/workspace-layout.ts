// WorkspaceLayout — where the emit pipeline writes each actuator type's output
// in the host repo's working tree. Defaults are designed for the "agents/<scenario>/"
// convention used by autocontext's scenario package layout; callers may override
// with `.autocontext/workspace.json` at the repo root.
//
// This module intentionally lives in `emit/` (not `actuators/`) because it is
// pipeline-level configuration — actuators receive a `WorkspaceLayout` as an
// argument; they never import it themselves (§3.2 import discipline).

import { existsSync, readFileSync } from "node:fs";
import { join } from "node:path";
import type { EnvironmentTag, Scenario } from "../contract/branded-ids.js";

export interface WorkspaceLayout {
  readonly scenarioDir: (scenario: Scenario, env: EnvironmentTag) => string;
  readonly promptSubdir: string;
  readonly policySubdir: string;
  readonly routingSubdir: string;
  readonly modelPointerSubdir: string;
}

/** Frozen immutable default used whenever no workspace.json is present or to fill in missing fields. */
const DEFAULTS = Object.freeze({
  promptSubdir: "prompts",
  policySubdir: "policies/tools",
  routingSubdir: "routing",
  modelPointerSubdir: "models/active",
  scenarioDirTemplate: "agents/${scenario}",
});

type PartialOverrides = {
  promptSubdir?: string;
  policySubdir?: string;
  routingSubdir?: string;
  modelPointerSubdir?: string;
  /** Template with ${scenario} and ${env} placeholders. Defaults to "agents/${scenario}". */
  scenarioDirTemplate?: string;
};

function makeScenarioDir(template: string): (scenario: Scenario, env: EnvironmentTag) => string {
  return (scenario, env) =>
    template.replace(/\$\{scenario\}/g, scenario).replace(/\$\{env\}/g, env);
}

export function defaultWorkspaceLayout(): WorkspaceLayout {
  return {
    scenarioDir: makeScenarioDir(DEFAULTS.scenarioDirTemplate),
    promptSubdir: DEFAULTS.promptSubdir,
    policySubdir: DEFAULTS.policySubdir,
    routingSubdir: DEFAULTS.routingSubdir,
    modelPointerSubdir: DEFAULTS.modelPointerSubdir,
  };
}

/**
 * Load the workspace layout rooted at `cwd`. Reads `<cwd>/.autocontext/workspace.json`
 * if present and merges any recognized fields on top of the defaults; unknown fields
 * are silently ignored for forward compatibility. Malformed JSON throws.
 */
export function loadWorkspaceLayout(cwd: string): WorkspaceLayout {
  const cfgPath = join(cwd, ".autocontext", "workspace.json");
  if (!existsSync(cfgPath)) return defaultWorkspaceLayout();

  let raw: string;
  try {
    raw = readFileSync(cfgPath, "utf-8");
  } catch (e) {
    throw new Error(`loadWorkspaceLayout: failed to read ${cfgPath}: ${e instanceof Error ? e.message : String(e)}`);
  }
  let parsed: unknown;
  try {
    parsed = JSON.parse(raw);
  } catch (e) {
    throw new Error(`loadWorkspaceLayout: malformed workspace.json at ${cfgPath}: ${e instanceof Error ? e.message : String(e)}`);
  }
  if (parsed === null || typeof parsed !== "object" || Array.isArray(parsed)) {
    throw new Error(`loadWorkspaceLayout: workspace.json must be a JSON object`);
  }

  const overrides = parsed as PartialOverrides;
  const template =
    typeof overrides.scenarioDirTemplate === "string"
      ? overrides.scenarioDirTemplate
      : DEFAULTS.scenarioDirTemplate;

  return {
    scenarioDir: makeScenarioDir(template),
    promptSubdir:
      typeof overrides.promptSubdir === "string" ? overrides.promptSubdir : DEFAULTS.promptSubdir,
    policySubdir:
      typeof overrides.policySubdir === "string" ? overrides.policySubdir : DEFAULTS.policySubdir,
    routingSubdir:
      typeof overrides.routingSubdir === "string" ? overrides.routingSubdir : DEFAULTS.routingSubdir,
    modelPointerSubdir:
      typeof overrides.modelPointerSubdir === "string"
        ? overrides.modelPointerSubdir
        : DEFAULTS.modelPointerSubdir,
  };
}
