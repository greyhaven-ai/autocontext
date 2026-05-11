import { existsSync, readFileSync, realpathSync, statSync } from "node:fs";
import { basename, dirname, relative, resolve } from "node:path";

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

export interface RuntimeContextBundleEntry {
  readonly entryId: string;
  readonly title: string;
  readonly content: string;
  readonly provenance: Readonly<Record<string, string>>;
  readonly metadata: Readonly<Record<string, string>>;
}

export interface RuntimeContextLayerBundle {
  readonly layer: RuntimeContextLayer;
  readonly entries: readonly RuntimeContextBundleEntry[];
}

export class RuntimeContextBundle {
  readonly layers: readonly RuntimeContextLayerBundle[];

  constructor(layers: readonly RuntimeContextLayerBundle[]) {
    this.layers = layers;
  }

  getLayer(key: RuntimeContextLayerKey): RuntimeContextLayerBundle {
    const layer = this.layers.find((candidate) => candidate.layer.key === key);
    if (!layer) throw new Error(`unknown runtime context layer: ${key}`);
    return layer;
  }

  allEntries(): RuntimeContextBundleEntry[] {
    return this.layers.flatMap((layer) => [...layer.entries]);
  }
}

export interface RuntimeContextAssemblyRequestOptions {
  readonly discovery: RuntimeContextDiscoveryRequest;
  readonly systemPolicy?: string;
  readonly roleInstructions?: string;
  readonly scenarioContext?: string;
  readonly knowledgeComponents?: Readonly<Record<string, string>>;
  readonly knowledgeInclude?: readonly string[];
  readonly knowledgeExclude?: readonly string[];
  readonly toolAffordances?: Readonly<Record<string, string>>;
  readonly sessionHistory?: readonly string[];
}

export class RuntimeContextAssemblyRequest {
  readonly discovery: RuntimeContextDiscoveryRequest;
  readonly systemPolicy: string;
  readonly roleInstructions: string;
  readonly scenarioContext: string;
  readonly knowledgeComponents: Readonly<Record<string, string>>;
  readonly knowledgeInclude?: readonly string[];
  readonly knowledgeExclude: readonly string[];
  readonly toolAffordances: Readonly<Record<string, string>>;
  readonly sessionHistory: readonly string[];

  constructor(opts: RuntimeContextAssemblyRequestOptions) {
    this.discovery = opts.discovery;
    this.systemPolicy = opts.systemPolicy ?? "";
    this.roleInstructions = opts.roleInstructions ?? "";
    this.scenarioContext = opts.scenarioContext ?? "";
    this.knowledgeComponents = opts.knowledgeComponents ?? {};
    this.knowledgeInclude = opts.knowledgeInclude;
    this.knowledgeExclude = opts.knowledgeExclude ?? [];
    this.toolAffordances = opts.toolAffordances ?? {};
    this.sessionHistory = opts.sessionHistory ?? [];
  }

  forChildTask(cwd: string): RuntimeContextAssemblyRequest {
    return new RuntimeContextAssemblyRequest({
      discovery: this.discovery.forChildTask(cwd),
      systemPolicy: this.systemPolicy,
      roleInstructions: this.roleInstructions,
      scenarioContext: this.scenarioContext,
      knowledgeComponents: this.knowledgeComponents,
      knowledgeInclude: this.knowledgeInclude,
      knowledgeExclude: this.knowledgeExclude,
      toolAffordances: this.toolAffordances,
      sessionHistory: this.sessionHistory,
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

export function assembleRuntimeContext(request: RuntimeContextAssemblyRequest): RuntimeContextBundle {
  const entriesByLayer: Partial<Record<RuntimeContextLayerKey, readonly RuntimeContextBundleEntry[]>> = {
    [RuntimeContextLayerKey.SYSTEM_POLICY]: singleTextEntry(
      "system_policy:default",
      "System Policy",
      request.systemPolicy,
      "system_policy",
    ),
    [RuntimeContextLayerKey.REPO_INSTRUCTIONS]: repoInstructionEntries(request.discovery),
    [RuntimeContextLayerKey.ROLE_INSTRUCTIONS]: singleTextEntry(
      "role_instructions:default",
      "Role Instructions",
      request.roleInstructions,
      "role_instructions",
    ),
    [RuntimeContextLayerKey.SCENARIO_CONTEXT]: singleTextEntry(
      "scenario_context:default",
      "Scenario Context",
      request.scenarioContext,
      "scenario_context",
    ),
    [RuntimeContextLayerKey.KNOWLEDGE]: knowledgeEntries(request),
    [RuntimeContextLayerKey.RUNTIME_SKILLS]: runtimeSkillEntries(request.discovery),
    [RuntimeContextLayerKey.TOOL_AFFORDANCES]: mappingEntries(request.toolAffordances, {
      entryIdPrefix: "tool_affordance",
      sourceType: "tool_affordance",
    }),
    [RuntimeContextLayerKey.SESSION_HISTORY]: sessionHistoryEntries(request.sessionHistory),
  };

  return new RuntimeContextBundle(
    RUNTIME_CONTEXT_LAYERS.map((layer) => ({
      layer,
      entries: entriesByLayer[layer.key] ?? [],
    })),
  );
}

function workspaceRoot(request: RuntimeContextDiscoveryRequest): string {
  return realpathSync(resolve(request.workspaceRoot));
}

function resolveCwd(root: string, cwd: string): string {
  const candidate = cwd.startsWith("/") ? resolve(root, cwd.slice(1)) : resolve(root, cwd);
  const resolved = resolvePossiblyMissingPath(candidate);
  if (!isPathWithinRoot(root, resolved)) {
    throw new Error(`Runtime context cwd escapes workspace root: ${cwd}`);
  }
  return resolved;
}

function resolvePossiblyMissingPath(path: string): string {
  let existing = resolve(path);
  const missingParts: string[] = [];

  while (!existsSync(existing)) {
    const parent = dirname(existing);
    if (parent === existing) break;
    missingParts.unshift(basename(existing));
    existing = parent;
  }

  return resolve(realpathSync(existing), ...missingParts);
}

function isPathWithinRoot(root: string, path: string): boolean {
  const rel = relative(root, path);
  return rel === "" || (rel !== ".." && !rel.startsWith("../") && !rel.startsWith("..\\"));
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

function singleTextEntry(
  entryId: string,
  title: string,
  content: string,
  sourceType: string,
): RuntimeContextBundleEntry[] {
  if (!content.trim()) return [];
  return [{ entryId, title, content, provenance: { sourceType }, metadata: {} }];
}

function repoInstructionEntries(request: RuntimeContextDiscoveryRequest): RuntimeContextBundleEntry[] {
  return discoverRepoInstructions(request).map((instruction) => ({
    entryId: `repo_instruction:${instruction.relativePath}`,
    title: instruction.relativePath,
    content: instruction.content,
    provenance: {
      sourceType: "repo_instruction",
      relativePath: instruction.relativePath,
      path: instruction.path,
    },
    metadata: {},
  }));
}

function knowledgeEntries(request: RuntimeContextAssemblyRequest): RuntimeContextBundleEntry[] {
  return mappingEntries(
    selectRuntimeKnowledgeComponents(request.knowledgeComponents, {
      include: request.knowledgeInclude,
      exclude: request.knowledgeExclude,
    }),
    { entryIdPrefix: "knowledge", sourceType: "knowledge_component", provenanceKey: "component" },
  );
}

function runtimeSkillEntries(request: RuntimeContextDiscoveryRequest): RuntimeContextBundleEntry[] {
  const root = workspaceRoot(request);
  return discoverRuntimeSkills(request).allManifests().map((manifest) => {
    const provenance: Record<string, string> = {
      sourceType: "runtime_skill",
      name: manifest.name,
      path: manifest.skillPath,
    };
    const relativePath = relativeToRoot(manifest.skillPath, root);
    if (relativePath) provenance.relativePath = relativePath;
    return {
      entryId: `runtime_skill:${manifest.name}`,
      title: manifest.name,
      content: manifest.description,
      provenance,
      metadata: { manifestFirst: "true" },
    };
  });
}

function mappingEntries(
  values: Readonly<Record<string, string>>,
  opts: { entryIdPrefix: string; sourceType: string; provenanceKey?: string },
): RuntimeContextBundleEntry[] {
  return Object.entries(values)
    .filter(([, value]) => value.trim().length > 0)
    .map(([key, value]) => ({
      entryId: `${opts.entryIdPrefix}:${key}`,
      title: key,
      content: value,
      provenance: { sourceType: opts.sourceType, [opts.provenanceKey ?? "name"]: key },
      metadata: {},
    }));
}

function sessionHistoryEntries(history: readonly string[]): RuntimeContextBundleEntry[] {
  const nonEmptyHistory = history
    .map((content, index) => ({ content, index: index + 1 }))
    .filter(({ content }) => content.trim().length > 0);
  return nonEmptyHistory.map(({ content, index }, visibleIndex) => ({
    entryId: `session_history:${index}`,
    title:
      nonEmptyHistory.length === 1
        ? "Recent Session History"
        : `Recent Session History #${visibleIndex + 1}`,
    content,
    provenance: { sourceType: "session_history", index: String(index) },
    metadata: {},
  }));
}

function relativeToRoot(path: string, root: string): string | null {
  const resolved = resolve(path);
  if (!isPathWithinRoot(root, resolved)) return null;
  return relative(root, resolved).split("\\").join("/");
}
