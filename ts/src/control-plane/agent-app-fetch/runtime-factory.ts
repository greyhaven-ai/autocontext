import type { AgentRuntime } from "../../runtimes/base.js";
import type { AgentAppFetchTarget } from "./index.js";

export interface AgentAppFetchRuntimeFactorySourceEntry {
  name: string;
  relativePath: string;
  extension: string;
  importSpecifier?: string;
}

export interface AgentAppFetchRuntimeFactoryPlanEntry extends AgentAppFetchRuntimeFactorySourceEntry {
  importSpecifier: string;
}

export interface AgentAppFetchRuntimeFactoryPlanOptions {
  entries: readonly AgentAppFetchRuntimeFactorySourceEntry[];
  runtimeDir?: string;
  moduleSpecifier?: (entry: AgentAppFetchRuntimeFactorySourceEntry) => string;
}

export interface AgentAppFetchRuntimeFactoryPlan {
  target: AgentAppFetchTarget;
  runtimeDir: string;
  entries: AgentAppFetchRuntimeFactoryPlanEntry[];
}

export interface AgentAppFetchRuntimeFactoryMetadata {
  readonly runtimeFactoryName?: string;
}

export type AgentAppFetchRuntimeFactory = (() => MaybePromise<AgentRuntime>) &
  AgentAppFetchRuntimeFactoryMetadata;
export type AgentAppFetchRuntimeFactoryModuleLoader = () => MaybePromise<unknown>;
export type AgentAppFetchRuntimeFactoryModuleMap = Record<
  string,
  AgentAppFetchRuntimeFactoryModuleLoader
>;

export interface AgentAppFetchLazyRuntimeOptions {
  name?: string;
}

type MaybePromise<T> = T | Promise<T>;

const DEFAULT_AGENT_APP_FETCH_RUNTIME_DIR = ".autoctx/runtimes";

export function planAgentAppFetchRuntimeFactories(
  options: AgentAppFetchRuntimeFactoryPlanOptions,
): AgentAppFetchRuntimeFactoryPlan {
  const runtimeDir = normalizeCatalogPath(
    options.runtimeDir ?? DEFAULT_AGENT_APP_FETCH_RUNTIME_DIR,
    "runtime factory directory",
  );
  const seenNames = new Set<string>();
  const entries = options.entries.map((entry) => {
    const name = normalizeRuntimeFactoryName(entry.name);
    if (seenNames.has(name)) {
      throw new Error(`Duplicate AutoContext runtime factory name: ${name}`);
    }
    seenNames.add(name);
    const relativePath = normalizeCatalogPath(entry.relativePath, "runtime factory path");
    if (!relativePath.startsWith(`${runtimeDir}/`)) {
      throw new Error(`Agent app Fetch runtime factory entries must be under ${runtimeDir}`);
    }
    if (relativePath.endsWith(".d.ts")) {
      throw new Error("Declaration files cannot be Fetch runtime factories");
    }
    const extension = normalizeExtension(entry.extension);
    const normalizedEntry: AgentAppFetchRuntimeFactorySourceEntry = {
      name,
      relativePath,
      extension,
    };
    if (entry.importSpecifier !== undefined) {
      normalizedEntry.importSpecifier = entry.importSpecifier;
    }
    return {
      ...normalizedEntry,
      importSpecifier:
        options.moduleSpecifier?.(normalizedEntry) ?? entry.importSpecifier ?? `./${relativePath}`,
    };
  });
  entries.sort((left, right) => left.name.localeCompare(right.name));
  return {
    target: "fetch",
    runtimeDir,
    entries,
  };
}

export function createAgentAppFetchRuntimeFactoryFromModuleMap(
  planOrEntries: AgentAppFetchRuntimeFactoryPlan | readonly AgentAppFetchRuntimeFactoryPlanEntry[],
  moduleMap: AgentAppFetchRuntimeFactoryModuleMap,
  name?: string,
): AgentAppFetchRuntimeFactory {
  const entries: readonly AgentAppFetchRuntimeFactoryPlanEntry[] = isRuntimeFactoryPlan(
    planOrEntries,
  )
    ? planOrEntries.entries
    : planOrEntries;
  const entry = resolveRuntimeFactoryEntry(entries, name);
  const factory = async () => {
    const loadModule = moduleMap[entry.name];
    if (!loadModule) {
      throw new Error(`Missing runtime factory module loader: ${entry.name}`);
    }
    const imported = unwrapRuntimeFactoryModule(await loadModule());
    const runtime = await imported();
    if (!isAgentRuntime(runtime)) {
      throw new Error(`AutoContext runtime factory '${entry.name}' must return an AgentRuntime`);
    }
    return runtime;
  };
  return withRuntimeFactoryName(factory, entry.name);
}

export function createAgentAppFetchLazyRuntime(
  factory: AgentAppFetchRuntimeFactory,
  options: AgentAppFetchLazyRuntimeOptions = {},
): AgentRuntime {
  let runtimePromise: Promise<AgentRuntime> | undefined;
  let loadedRuntimeName: string | undefined;
  const loadRuntime = async () => {
    runtimePromise ??= Promise.resolve(factory()).then((runtime) => {
      loadedRuntimeName = runtime.name;
      return runtime;
    });
    return await runtimePromise;
  };
  return {
    get name() {
      return (
        loadedRuntimeName ??
        options.name ??
        factory.runtimeFactoryName ??
        "agent-app-fetch-runtime-factory"
      );
    },
    generate: async (generateOptions) => (await loadRuntime()).generate(generateOptions),
    revise: async (reviseOptions) => (await loadRuntime()).revise(reviseOptions),
    close: () => {
      if (runtimePromise) void runtimePromise.then((runtime) => runtime.close?.());
    },
  };
}

function withRuntimeFactoryName(
  factory: () => MaybePromise<AgentRuntime>,
  runtimeFactoryName: string,
): AgentAppFetchRuntimeFactory {
  return Object.defineProperty(factory, "runtimeFactoryName", {
    value: runtimeFactoryName,
    enumerable: false,
  }) as AgentAppFetchRuntimeFactory;
}

function isRuntimeFactoryPlan(
  value: AgentAppFetchRuntimeFactoryPlan | readonly AgentAppFetchRuntimeFactoryPlanEntry[],
): value is AgentAppFetchRuntimeFactoryPlan {
  return !Array.isArray(value);
}

function resolveRuntimeFactoryEntry(
  entries: readonly AgentAppFetchRuntimeFactoryPlanEntry[],
  name: string | undefined,
): AgentAppFetchRuntimeFactoryPlanEntry {
  if (name === undefined) {
    if (entries.length === 1) return entries[0]!;
    throw new Error("Runtime factory name is required when multiple factories are planned");
  }
  const entry = entries.find((candidate) => candidate.name === name);
  if (!entry) {
    const available = entries.map((candidate) => candidate.name).join(", ");
    throw new Error(
      available
        ? `Runtime factory not found: ${name}. Available: ${available}`
        : `Runtime factory not found: ${name}. No factories registered`,
    );
  }
  return entry;
}

function unwrapRuntimeFactoryModule(value: unknown): AgentAppFetchRuntimeFactory {
  if (isRuntimeFactory(value)) return value;
  const moduleRecord = isRecord(value) ? value : { default: value };
  if (isRuntimeFactory(moduleRecord.default)) return moduleRecord.default;
  if (isRuntimeFactory(moduleRecord.createRuntime)) return moduleRecord.createRuntime;
  const nestedDefault = readOptionalRecord(moduleRecord.default);
  if (nestedDefault) {
    if (isRuntimeFactory(nestedDefault.default)) return nestedDefault.default;
    if (isRuntimeFactory(nestedDefault.createRuntime)) return nestedDefault.createRuntime;
  }
  throw new Error("Fetch runtime factory modules must export a factory function");
}

function normalizeCatalogPath(value: string, label: string): string {
  const trimmed = value
    .trim()
    .replace(/\\/g, "/")
    .replace(/^\.\/+/, "");
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

function normalizeRuntimeFactoryName(name: string): string {
  const trimmed = name.trim();
  if (!trimmed || !/^[A-Za-z0-9._-]+$/u.test(trimmed)) {
    throw new Error("AutoContext runtime factory names must be non-empty path-safe identifiers");
  }
  return trimmed;
}

function normalizeExtension(extension: string): string {
  const trimmed = extension.trim();
  if (!trimmed.startsWith(".") || trimmed.includes("/")) {
    throw new Error("AutoContext runtime factory extensions must start with '.'");
  }
  return trimmed;
}

function readOptionalRecord(value: unknown): Record<string, unknown> | undefined {
  return isRecord(value) ? { ...value } : undefined;
}

function isRuntimeFactory(value: unknown): value is AgentAppFetchRuntimeFactory {
  return typeof value === "function";
}

function isAgentRuntime(value: unknown): value is AgentRuntime {
  return (
    isRecord(value) &&
    typeof value.name === "string" &&
    typeof value.generate === "function" &&
    typeof value.revise === "function"
  );
}

function isRecord(value: unknown): value is Record<string, unknown> {
  return typeof value === "object" && value !== null && !Array.isArray(value);
}
