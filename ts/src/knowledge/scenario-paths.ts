/** Per-scenario path containment (mirrors Python autocontext/storage/scenario_paths.py). */
import { resolve, sep } from "node:path";

export function normalizeScenarioSegment(scenario: string): string {
  const s = scenario.trim();
  if (!s) throw new Error("scenario name is required");
  if (s.includes("/") || s.includes("\\") || s === "." || s === "..") {
    throw new Error(`scenario name must be a single path segment: ${scenario}`);
  }
  return s;
}

/** Resolve a scenario directory and ensure it stays under knowledgeRoot. Throws on unsafe input. */
export function resolveScenarioRoot(knowledgeRoot: string, scenario: string): string {
  const normalized = normalizeScenarioSegment(scenario);
  const root = resolve(knowledgeRoot);
  const candidate = resolve(knowledgeRoot, normalized);
  if (candidate === root) {
    throw new Error(`scenario name must name a subdirectory: ${scenario}`);
  }
  if (!candidate.startsWith(root + sep)) {
    throw new Error(`scenario name escapes knowledge root: ${scenario}`);
  }
  return candidate;
}
