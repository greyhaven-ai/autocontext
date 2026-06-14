import type {
  RuntimeExecOptions,
  RuntimeExecResult,
  RuntimeFileStat,
  RuntimeScopeOptions,
  RuntimeToolGrant,
  RuntimeWorkspaceEnv,
} from "../../runtimes/workspace-env.js";

export type AgentAppFetchWorkspaceEntryKind = "file" | "directory";
export type AgentAppFetchWorkspaceStorePersistence = "request_memory" | "host_durable";
export type AgentAppFetchWorkspaceStoreConsistency = "read_your_writes_after_write";
export type AgentAppFetchWorkspaceStoreListing = "lexicographic";

export interface AgentAppFetchWorkspaceStoreCapabilities {
  persistence: AgentAppFetchWorkspaceStorePersistence;
  consistency: AgentAppFetchWorkspaceStoreConsistency;
  listing: AgentAppFetchWorkspaceStoreListing;
  unsupportedOperations: readonly string[];
}

export interface AgentAppFetchWorkspaceEntryStat {
  path: string;
  kind: AgentAppFetchWorkspaceEntryKind;
  size: number;
  mtime: Date;
}

export interface AgentAppFetchWorkspaceStore {
  readonly capabilities?: AgentAppFetchWorkspaceStoreCapabilities;
  readFile(path: string): Promise<Uint8Array>;
  writeFile(path: string, content: Uint8Array): Promise<void>;
  stat(path: string): Promise<AgentAppFetchWorkspaceEntryStat>;
  readdir(path: string): Promise<string[]>;
  exists(path: string): Promise<boolean>;
  mkdir(path: string, options?: { recursive?: boolean }): Promise<void>;
  rm(path: string, options?: { recursive?: boolean; force?: boolean }): Promise<void>;
}

export interface AgentAppFetchWorkspaceEnvOptions {
  store: AgentAppFetchWorkspaceStore;
  cwd?: string;
  tools?: readonly RuntimeToolGrant[];
}

type EdgeMemoryFile = {
  content: Uint8Array;
  mtime: Date;
};

type EdgeMemoryState = {
  files: Map<string, EdgeMemoryFile>;
  dirs: Map<string, Date>;
};

const AGENT_APP_FETCH_WORKSPACE_STORE_CAPABILITIES = {
  persistence: "request_memory",
  consistency: "read_your_writes_after_write",
  listing: "lexicographic",
  unsupportedOperations: ["exec"],
} as const satisfies AgentAppFetchWorkspaceStoreCapabilities;

export function createAgentAppFetchWorkspaceEnv(
  options: AgentAppFetchWorkspaceEnvOptions,
): RuntimeWorkspaceEnv {
  return new AgentAppFetchWorkspaceEnv(
    options.store,
    normalizeVirtualPath(options.cwd ?? "/", "/"),
    options.tools,
  );
}

export function createInMemoryAgentAppFetchWorkspaceStore(): AgentAppFetchWorkspaceStore {
  return new InMemoryAgentAppFetchWorkspaceStore();
}

export function createEdgeInMemoryWorkspaceEnv(
  options: { cwd?: string } = {},
): RuntimeWorkspaceEnv {
  const store = createInMemoryAgentAppFetchWorkspaceStore();
  const cwd = normalizeVirtualPath(options.cwd ?? "/", "/");
  void store.mkdir(cwd, { recursive: true });
  return createAgentAppFetchWorkspaceEnv({ store, cwd });
}

class AgentAppFetchWorkspaceEnv implements RuntimeWorkspaceEnv {
  readonly #store: AgentAppFetchWorkspaceStore;
  readonly cwd: string;
  readonly tools?: readonly RuntimeToolGrant[];

  constructor(
    store: AgentAppFetchWorkspaceStore,
    cwd: string,
    tools?: readonly RuntimeToolGrant[],
  ) {
    this.#store = store;
    this.cwd = normalizeVirtualPath(cwd, "/");
    this.tools = tools;
  }

  exec(_command: string, _options: RuntimeExecOptions = {}): Promise<RuntimeExecResult> {
    return Promise.reject(
      new Error(
        "Runtime command execution is unavailable in the generic Fetch agent app workspace",
      ),
    );
  }

  scope(options: RuntimeScopeOptions = {}): Promise<RuntimeWorkspaceEnv> {
    try {
      return Promise.resolve(
        new AgentAppFetchWorkspaceEnv(
          this.#store,
          normalizeVirtualPath(options.cwd ?? this.cwd, this.cwd),
          options.tools ?? this.tools,
        ),
      );
    } catch (error) {
      return Promise.reject(error);
    }
  }

  async readFile(filePath: string): Promise<string> {
    return new TextDecoder().decode(await this.readFileBytes(filePath));
  }

  async readFileBytes(filePath: string): Promise<Uint8Array> {
    return cloneBytes(await this.#store.readFile(this.resolvePath(filePath)));
  }

  async writeFile(filePath: string, content: string | Uint8Array): Promise<void> {
    const bytes =
      typeof content === "string" ? new TextEncoder().encode(content) : cloneBytes(content);
    await this.#store.writeFile(this.resolvePath(filePath), bytes);
  }

  async stat(filePath: string): Promise<RuntimeFileStat> {
    const stat = await this.#store.stat(this.resolvePath(filePath));
    return {
      isFile: stat.kind === "file",
      isDirectory: stat.kind === "directory",
      isSymbolicLink: false,
      size: stat.size,
      mtime: new Date(stat.mtime),
    };
  }

  async readdir(dirPath: string): Promise<string[]> {
    return await this.#store.readdir(this.resolvePath(dirPath));
  }

  async exists(filePath: string): Promise<boolean> {
    return await this.#store.exists(this.resolvePath(filePath));
  }

  async mkdir(dirPath: string, options: { recursive?: boolean } = {}): Promise<void> {
    await this.#store.mkdir(this.resolvePath(dirPath), options);
  }

  async rm(
    filePath: string,
    options: { recursive?: boolean; force?: boolean } = {},
  ): Promise<void> {
    await this.#store.rm(this.resolvePath(filePath), options);
  }

  resolvePath(filePath: string): string {
    return normalizeVirtualPath(filePath, this.cwd);
  }

  async cleanup(): Promise<void> {
    // Caller-owned stores have no request-lifetime resources to release here.
  }
}

class InMemoryAgentAppFetchWorkspaceStore implements AgentAppFetchWorkspaceStore {
  readonly capabilities = AGENT_APP_FETCH_WORKSPACE_STORE_CAPABILITIES;
  readonly #state = createEdgeMemoryState();

  readFile(path: string): Promise<Uint8Array> {
    const resolved = normalizeVirtualPath(path, "/");
    const file = this.#state.files.get(resolved);
    if (!file) return Promise.reject(new Error(`File not found: ${path}`));
    return Promise.resolve(cloneBytes(file.content));
  }

  writeFile(path: string, content: Uint8Array): Promise<void> {
    try {
      writeEdgeMemoryFile(this.#state, normalizeVirtualPath(path, "/"), content);
      return Promise.resolve();
    } catch (error) {
      return Promise.reject(error);
    }
  }

  stat(path: string): Promise<AgentAppFetchWorkspaceEntryStat> {
    const resolved = normalizeVirtualPath(path, "/");
    const file = this.#state.files.get(resolved);
    if (file) {
      return Promise.resolve({
        path: resolved,
        kind: "file",
        size: file.content.byteLength,
        mtime: new Date(file.mtime),
      });
    }
    const dirMtime = this.#state.dirs.get(resolved);
    if (dirMtime) {
      return Promise.resolve({
        path: resolved,
        kind: "directory",
        size: 0,
        mtime: new Date(dirMtime),
      });
    }
    return Promise.reject(new Error(`Path not found: ${path}`));
  }

  readdir(path: string): Promise<string[]> {
    const dir = normalizeVirtualPath(path, "/");
    if (!this.#state.dirs.has(dir)) {
      return Promise.reject(new Error(`Directory not found: ${path}`));
    }
    const names = new Set<string>();
    for (const filePath of this.#state.files.keys()) {
      if (parentDir(filePath) === dir) names.add(baseName(filePath));
    }
    for (const dirPath of this.#state.dirs.keys()) {
      if (dirPath !== dir && parentDir(dirPath) === dir) names.add(baseName(dirPath));
    }
    return Promise.resolve([...names].sort((left, right) => left.localeCompare(right)));
  }

  exists(path: string): Promise<boolean> {
    const resolved = normalizeVirtualPath(path, "/");
    return Promise.resolve(this.#state.files.has(resolved) || this.#state.dirs.has(resolved));
  }

  mkdir(path: string, options: { recursive?: boolean } = {}): Promise<void> {
    const resolved = normalizeVirtualPath(path, "/");
    const parent = parentDir(resolved);
    if (this.#state.files.has(resolved)) {
      return Promise.reject(new Error(`File exists: ${resolved}`));
    }
    if (this.#state.dirs.has(resolved)) {
      return options.recursive
        ? Promise.resolve()
        : Promise.reject(new Error(`Directory exists: ${resolved}`));
    }
    if (!options.recursive && !this.#state.dirs.has(parent)) {
      return Promise.reject(new Error(`Parent directory not found: ${parent}`));
    }
    try {
      ensureDir(this.#state, options.recursive ? resolved : parent);
      this.#state.dirs.set(resolved, new Date());
      return Promise.resolve();
    } catch (error) {
      return Promise.reject(error);
    }
  }

  rm(path: string, options: { recursive?: boolean; force?: boolean } = {}): Promise<void> {
    const resolved = normalizeVirtualPath(path, "/");
    if (resolved === "/") return this.#rmRoot(path, options);
    if (this.#state.files.delete(resolved)) return Promise.resolve();
    if (this.#state.dirs.has(resolved)) {
      const childPrefix = `${resolved}/`;
      const hasChildren = [...this.#state.files.keys(), ...this.#state.dirs.keys()].some(
        (entryPath) => entryPath !== resolved && entryPath.startsWith(childPrefix),
      );
      if (hasChildren && !options.recursive) {
        return Promise.reject(new Error(`Directory not empty: ${path}`));
      }
      for (const filePath of [...this.#state.files.keys()]) {
        if (filePath.startsWith(childPrefix)) this.#state.files.delete(filePath);
      }
      for (const dirPath of [...this.#state.dirs.keys()]) {
        if (dirPath !== "/" && (dirPath === resolved || dirPath.startsWith(childPrefix))) {
          this.#state.dirs.delete(dirPath);
        }
      }
      return Promise.resolve();
    }
    return options.force ? Promise.resolve() : Promise.reject(new Error(`Path not found: ${path}`));
  }

  #rmRoot(path: string, options: { recursive?: boolean; force?: boolean }): Promise<void> {
    if (!options.recursive) {
      return Promise.reject(new Error(`Directory not empty: ${path}`));
    }
    this.#state.files.clear();
    for (const dirPath of [...this.#state.dirs.keys()]) {
      if (dirPath !== "/") this.#state.dirs.delete(dirPath);
    }
    this.#state.dirs.set("/", new Date());
    return Promise.resolve();
  }
}

function createEdgeMemoryState(): EdgeMemoryState {
  return { files: new Map(), dirs: new Map([["/", new Date()]]) };
}

function writeEdgeMemoryFile(state: EdgeMemoryState, resolved: string, content: Uint8Array): void {
  if (state.dirs.has(resolved)) {
    throw new Error(`Is a directory: ${resolved}`);
  }
  ensureDir(state, parentDir(resolved));
  state.files.set(resolved, {
    content: cloneBytes(content),
    mtime: new Date(),
  });
}

function cloneBytes(content: Uint8Array): Uint8Array {
  return new Uint8Array(content);
}

function ensureDir(state: EdgeMemoryState, dirPath: string): void {
  let current = "/";
  state.dirs.set(current, state.dirs.get(current) ?? new Date());
  for (const segment of dirPath.split("/")) {
    if (!segment) continue;
    current = current === "/" ? `/${segment}` : `${current}/${segment}`;
    if (state.files.has(current)) {
      throw new Error(`Not a directory: ${current}`);
    }
    state.dirs.set(current, state.dirs.get(current) ?? new Date());
  }
}

function normalizeVirtualPath(filePath: string, cwd: string): string {
  const base = filePath.startsWith("/") ? filePath : `${cwd.replace(/\/$/, "")}/${filePath}`;
  const parts: string[] = [];
  for (const segment of base.split("/")) {
    if (!segment || segment === ".") continue;
    if (segment === "..") {
      parts.pop();
      continue;
    }
    parts.push(segment);
  }
  return `/${parts.join("/")}`;
}

function parentDir(filePath: string): string {
  if (filePath === "/") return "/";
  const index = filePath.lastIndexOf("/");
  return index <= 0 ? "/" : filePath.slice(0, index);
}

function baseName(filePath: string): string {
  const index = filePath.lastIndexOf("/");
  return index === -1 ? filePath : filePath.slice(index + 1);
}
