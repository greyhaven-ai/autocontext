import type {
  AutoctxAgentHandler,
  AutoctxLoadedAgent,
  MaybePromise,
} from "../../agent-runtime/index.js";
import type {
  AgentAppFetchAgentExtension,
  AgentAppFetchCatalogEntry,
  AgentAppFetchTarget,
} from "./index.js";

export interface AgentAppFetchCatalogSourceEntry {
  name: string;
  relativePath: string;
  extension: AgentAppFetchAgentExtension | string;
  triggers?: Record<string, unknown>;
  importSpecifier?: string;
}

export interface AgentAppFetchCatalogPlanEntry extends AgentAppFetchCatalogSourceEntry {
  importSpecifier: string;
}

export interface AgentAppFetchCatalogPlanOptions {
  entries: readonly AgentAppFetchCatalogSourceEntry[];
  handlerDir?: string;
  moduleSpecifier?: (entry: AgentAppFetchCatalogSourceEntry) => string;
}

export interface AgentAppFetchCatalogPlan {
  target: AgentAppFetchTarget;
  handlerDir: string;
  routes: AgentAppFetchRoute[];
  entries: AgentAppFetchCatalogPlanEntry[];
}

export type AgentAppFetchRoute = "GET /manifest" | "GET /agents" | "POST /agents/:agent/invoke";
export type AgentAppFetchModuleLoader = () => MaybePromise<unknown>;
export type AgentAppFetchModuleMap = Record<string, AgentAppFetchModuleLoader>;

export interface RenderAgentAppFetchModuleMapEntrypointOptions {
  packageSpecifier?: string;
}

const DEFAULT_AGENT_APP_FETCH_HANDLER_DIR = ".autoctx/agents";
const AGENT_APP_FETCH_ROUTES: AgentAppFetchRoute[] = [
  "GET /manifest",
  "GET /agents",
  "POST /agents/:agent/invoke",
];

export function planAgentAppFetchCatalog(
  options: AgentAppFetchCatalogPlanOptions,
): AgentAppFetchCatalogPlan {
  const handlerDir = normalizeCatalogPath(
    options.handlerDir ?? DEFAULT_AGENT_APP_FETCH_HANDLER_DIR,
    "handler directory",
  );
  const seenNames = new Set<string>();
  const entries = options.entries.map((entry) => {
    const name = normalizeAgentName(entry.name);
    if (seenNames.has(name)) {
      throw new Error(`Duplicate AutoContext agent name: ${name}`);
    }
    seenNames.add(name);
    const relativePath = normalizeCatalogPath(entry.relativePath, "agent relative path");
    if (!relativePath.startsWith(`${handlerDir}/`)) {
      throw new Error(`Agent app Fetch catalog entries must be under ${handlerDir}`);
    }
    if (relativePath.endsWith(".d.ts")) {
      throw new Error("Declaration files cannot be agent app handlers");
    }
    const extension = normalizeExtension(entry.extension);
    const normalizedEntry: AgentAppFetchCatalogSourceEntry = {
      name,
      relativePath,
      extension,
    };
    if (entry.importSpecifier !== undefined) {
      normalizedEntry.importSpecifier = entry.importSpecifier;
    }
    const triggers = cloneRecord(entry.triggers);
    if (triggers) normalizedEntry.triggers = triggers;
    return {
      ...normalizedEntry,
      importSpecifier:
        options.moduleSpecifier?.(normalizedEntry) ?? entry.importSpecifier ?? `./${relativePath}`,
    };
  });
  entries.sort((left, right) => left.name.localeCompare(right.name));
  return {
    target: "fetch",
    handlerDir,
    routes: [...AGENT_APP_FETCH_ROUTES],
    entries,
  };
}

export function createAgentAppFetchCatalogFromModuleMap<Payload = unknown, Result = unknown>(
  planOrEntries: AgentAppFetchCatalogPlan | readonly AgentAppFetchCatalogPlanEntry[],
  moduleMap: AgentAppFetchModuleMap,
): AgentAppFetchCatalogEntry<Payload, Result>[] {
  const entries: readonly AgentAppFetchCatalogPlanEntry[] = isAgentAppFetchCatalogPlan(
    planOrEntries,
  )
    ? planOrEntries.entries
    : planOrEntries;
  return entries.map((entry) => ({
    name: entry.name,
    relativePath: entry.relativePath,
    extension: entry.extension,
    triggers: cloneRecord(entry.triggers),
    load: async () => loadAgentAppFetchModuleEntry<Payload, Result>(entry, moduleMap),
  }));
}

export function renderAgentAppFetchModuleMapEntrypoint(
  plan: AgentAppFetchCatalogPlan,
  options: RenderAgentAppFetchModuleMapEntrypointOptions = {},
): string {
  const packageSpecifier = options.packageSpecifier ?? "autoctx/control-plane/agent-app-fetch";
  const moduleMapEntries = plan.entries.map(
    (entry) =>
      `  ${renderObjectKey(entry.name)}: () => import(${JSON.stringify(entry.importSpecifier)}),`,
  );
  return [
    `import { createAgentAppFetchCatalogFromModuleMap, createAgentAppFetchHandler } from ${JSON.stringify(packageSpecifier)};`,
    "",
    `export const agentAppFetchCatalogPlan = ${JSON.stringify(plan, null, 2)};`,
    "",
    "export const agentAppFetchModuleMap = {",
    ...moduleMapEntries,
    "};",
    "",
    "export const agentAppFetchCatalog = createAgentAppFetchCatalogFromModuleMap(",
    "  agentAppFetchCatalogPlan,",
    "  agentAppFetchModuleMap,",
    ");",
    "",
    "export const fetch = createAgentAppFetchHandler({ catalog: agentAppFetchCatalog });",
    "",
    "export default { fetch };",
    "",
  ].join("\n");
}

function normalizeCatalogPath(value: string, label: string): string {
  const trimmed = value
    .trim()
    .replace(/\\/g, "/")
    .replace(/^\.\/+/u, "");
  if (!trimmed) throw new Error(`AutoContext ${label} must be non-empty`);
  if (trimmed.startsWith("/")) throw new Error(`AutoContext ${label} must be relative`);
  const parts: string[] = [];
  for (const segment of trimmed.split("/")) {
    if (!segment || segment === ".") continue;
    if (segment === "..") {
      throw new Error(`AutoContext ${label} cannot contain parent directory segments`);
    }
    parts.push(segment);
  }
  if (parts.length === 0) throw new Error(`AutoContext ${label} must be non-empty`);
  return parts.join("/");
}

function normalizeAgentName(name: string): string {
  const trimmed = name.trim();
  if (!trimmed || !/^[A-Za-z0-9._-]+$/u.test(trimmed)) {
    throw new Error("AutoContext agent names must be non-empty path-safe identifiers");
  }
  return trimmed;
}

function normalizeExtension(extension: string): string {
  const trimmed = extension.trim();
  if (!trimmed.startsWith(".") || trimmed.includes("/")) {
    throw new Error(
      "AutoContext agent extensions must start with '.' and contain no path segments",
    );
  }
  return trimmed;
}

function isAgentAppFetchCatalogPlan(
  value: AgentAppFetchCatalogPlan | readonly AgentAppFetchCatalogPlanEntry[],
): value is AgentAppFetchCatalogPlan {
  return !Array.isArray(value);
}

async function loadAgentAppFetchModuleEntry<Payload, Result>(
  entry: AgentAppFetchCatalogPlanEntry,
  moduleMap: AgentAppFetchModuleMap,
): Promise<AutoctxLoadedAgent<Payload, Result>> {
  const loadModule = moduleMap[entry.name];
  if (!loadModule) {
    throw new Error(`Missing module loader for AutoContext agent: ${entry.name}`);
  }
  const imported = unwrapAgentAppFetchModule(await loadModule());
  if (!isAutoctxAgentHandler<Payload, Result>(imported.default)) {
    throw new Error(
      `AutoContext agent '${entry.name}' module must export a default handler function`,
    );
  }
  return {
    name: entry.name,
    relativePath: entry.relativePath,
    handler: imported.default,
    triggers: readOptionalRecord(imported.triggers) ?? entry.triggers,
  };
}

function unwrapAgentAppFetchModule(value: unknown): Record<string, unknown> {
  const moduleRecord = isRecord(value) ? value : { default: value };
  const nestedDefault = readOptionalRecord(moduleRecord.default);
  if (nestedDefault && isAutoctxAgentHandler(nestedDefault.default)) return nestedDefault;
  return moduleRecord;
}

function cloneRecord(value: unknown): Record<string, unknown> | undefined {
  return isRecord(value) ? { ...value } : undefined;
}

function readOptionalRecord(value: unknown): Record<string, unknown> | undefined {
  return isRecord(value) ? { ...value } : undefined;
}

function isAutoctxAgentHandler<Payload, Result>(
  value: unknown,
): value is AutoctxAgentHandler<Payload, Result> {
  return typeof value === "function";
}

function isRecord(value: unknown): value is Record<string, unknown> {
  return typeof value === "object" && value !== null && !Array.isArray(value);
}

function renderObjectKey(value: string): string {
  return /^[A-Za-z_$][A-Za-z0-9_$]*$/u.test(value) ? value : JSON.stringify(value);
}
