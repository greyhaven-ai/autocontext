import { existsSync, readFileSync, statSync } from "node:fs";
import { relative, resolve } from "node:path";

import { SkillRegistry } from "./skill-registry.js";

export const REPO_INSTRUCTION_FILENAMES = ["AGENTS.md", "CLAUDE.md"] as const;
export const RUNTIME_SKILL_DIRS = [".autoctx/skills", ".claude/skills", ".codex/skills", "skills"] as const;

export const RuntimeContextLayerKey = {
  SYSTEM_POLICY: "system_policy",
  REPO_INSTRUCTIONS: "repo_instructions",
  ROLE_INSTRUCTIONS: "role_instructions",
  SCENARIO_CONTEXT: "scenario_context",
  KNOWLEDGE: "knowledge",
  RUNTIME_SKILLS: "runtime_skills",
  TOOL_AFFORDANCES: "tool_affordances",
  SESSION_HISTORY: "session_history",
} as const;
export type RuntimeContextLayerKey = (typeof RuntimeContextLayerKey)[keyof typeof RuntimeContextLayerKey];

export interface RuntimeContextLayer {
  readonly key: RuntimeContextLayerKey;
  readonly order: number;
  readonly owner: string;
  readonly persistence: string;
  readonly budget: string;
  readonly childTaskBehavior: string;
}

export interface RepoInstruction {
  readonly path: string;
  readonly relativePath: string;
  readonly content: string;
}

export interface RuntimeContextDiscoveryRequestOptions {
  readonly workspaceRoot: string;
  readonly cwd?: string;
  readonly configuredSkillRoots?: readonly string[];
}

export class RuntimeContextDiscoveryRequest {
  readonly workspaceRoot: string;
  readonly cwd: string;
  readonly configuredSkillRoots: readonly string[];

  constructor(opts: RuntimeContextDiscoveryRequestOptions) {
    this.workspaceRoot = opts.workspaceRoot;
    this.cwd = opts.cwd ?? "/";
    this.configuredSkillRoots = opts.configuredSkillRoots ?? [];
  }

  forChildTask(cwd: string): RuntimeContextDiscoveryRequest {
    return new RuntimeContextDiscoveryRequest({
      workspaceRoot: this.workspaceRoot,
      cwd,
      configuredSkillRoots: this.configuredSkillRoots,
    });
  }
}

export const RUNTIME_CONTEXT_LAYERS: readonly RuntimeContextLayer[] = [
  {
    key: RuntimeContextLayerKey.SYSTEM_POLICY,
    order: 1,
    owner: "runtime",
    persistence: "bundled",
    budget: "protected",
    childTaskBehavior: "inherit",
  },
  {
    key: RuntimeContextLayerKey.REPO_INSTRUCTIONS,
    order: 2,
    owner: "workspace",
    persistence: "repo",
    budget: "protected",
    childTaskBehavior: "recompute_from_child_cwd",
  },
  {
    key: RuntimeContextLayerKey.ROLE_INSTRUCTIONS,
    order: 3,
    owner: "autocontext",
    persistence: "bundled",
    budget: "protected",
    childTaskBehavior: "inherit_or_override_by_role",
  },
  {
    key: RuntimeContextLayerKey.SCENARIO_CONTEXT,
    order: 4,
    owner: "scenario",
    persistence: "run",
    budget: "protected",
    childTaskBehavior: "inherit_task_slice",
  },
  {
    key: RuntimeContextLayerKey.KNOWLEDGE,
    order: 5,
    owner: "knowledge",
    persistence: "knowledge",
    budget: "compress",
    childTaskBehavior: "include_applicable_knowledge",
  },
  {
    key: RuntimeContextLayerKey.RUNTIME_SKILLS,
    order: 6,
    owner: "workspace",
    persistence: "repo_or_skill_store",
    budget: "manifest_first",
    childTaskBehavior: "recompute_from_child_cwd",
  },
  {
    key: RuntimeContextLayerKey.TOOL_AFFORDANCES,
    order: 7,
    owner: "runtime",
    persistence: "ephemeral",
    budget: "summarize",
    childTaskBehavior: "inherit_scoped_grants",
  },
  {
    key: RuntimeContextLayerKey.SESSION_HISTORY,
    order: 8,
    owner: "runtime_session",
    persistence: "runtime_session_log",
    budget: "compact",
    childTaskBehavior: "recompute_from_child_session",
  },
] as const;

export const RUNTIME_CONTEXT_LAYER_KEYS = RUNTIME_CONTEXT_LAYERS.map((layer) => layer.key);

export function discoverRepoInstructions(request: RuntimeContextDiscoveryRequest): RepoInstruction[] {
  const root = workspaceRoot(request);
  const cwd = resolveCwd(root, request.cwd);
  const instructions: RepoInstruction[] = [];
  for (const dir of ancestorDirs(root, cwd, false)) {
    for (const filename of REPO_INSTRUCTION_FILENAMES) {
      const path = resolve(dir, filename);
      if (!isFile(path)) continue;
      instructions.push({
        path,
        relativePath: relative(root, path).split("\\").join("/"),
        content: readFileSync(path, "utf-8"),
      });
    }
  }
  return instructions;
}

export function runtimeSkillDiscoveryRoots(request: RuntimeContextDiscoveryRequest): string[] {
  const root = workspaceRoot(request);
  const cwd = resolveCwd(root, request.cwd);
  const roots: string[] = [];
  const seen = new Set<string>();

  for (const configuredRoot of request.configuredSkillRoots) {
    appendExistingUniqueDir(roots, seen, resolveConfiguredRoot(root, configuredRoot));
  }

  for (const dir of ancestorDirs(root, cwd, true)) {
    for (const skillDir of RUNTIME_SKILL_DIRS) {
      appendExistingUniqueDir(roots, seen, resolve(dir, skillDir));
    }
  }
  return roots;
}

export function discoverRuntimeSkills(request: RuntimeContextDiscoveryRequest): SkillRegistry {
  const registry = new SkillRegistry();
  for (const root of runtimeSkillDiscoveryRoots(request)) {
    registry.discover(root);
  }
  return registry;
}

export function selectRuntimeKnowledgeComponents(
  components: Record<string, string>,
  opts: { include?: readonly string[]; exclude?: readonly string[] } = {},
): Record<string, string> {
  const allowed = opts.include ? new Set(opts.include) : null;
  const blocked = new Set(opts.exclude ?? []);
  const selected: Record<string, string> = {};
  for (const [key, value] of Object.entries(components)) {
    if (allowed && !allowed.has(key)) continue;
    if (blocked.has(key) || !value) continue;
    selected[key] = value;
  }
  return selected;
}

function workspaceRoot(request: RuntimeContextDiscoveryRequest): string {
  return resolve(request.workspaceRoot);
}

function resolveCwd(root: string, cwd: string): string {
  const candidate = cwd.startsWith("/") ? resolve(root, cwd.slice(1)) : resolve(root, cwd);
  const resolved = resolve(candidate);
  const rel = relative(root, resolved);
  if (rel === "") return resolved;
  if (rel === ".." || rel.startsWith("../") || rel.startsWith("..\\")) {
    throw new Error(`Runtime context cwd escapes workspace root: ${cwd}`);
  }
  return resolved;
}

function resolveConfiguredRoot(root: string, skillRoot: string): string {
  return skillRoot.startsWith("/") ? resolve(skillRoot) : resolve(root, skillRoot);
}

function ancestorDirs(root: string, cwd: string, nearestFirst: boolean): string[] {
  const dirs: string[] = [];
  let current = cwd;
  while (true) {
    dirs.push(current);
    if (current === root) break;
    current = resolve(current, "..");
  }
  return nearestFirst ? dirs : dirs.reverse();
}

function appendExistingUniqueDir(roots: string[], seen: Set<string>, path: string): void {
  if (seen.has(path) || !isDirectory(path)) return;
  seen.add(path);
  roots.push(path);
}

function isDirectory(path: string): boolean {
  return existsSync(path) && statSync(path).isDirectory();
}

function isFile(path: string): boolean {
  return existsSync(path) && statSync(path).isFile();
}
