import { spawn } from "node:child_process";
import { promises as fs } from "node:fs";
import path from "node:path";

export interface RuntimeExecOptions {
  cwd?: string;
  env?: Record<string, string>;
  timeoutMs?: number;
  signal?: AbortSignal;
}

export interface RuntimeExecResult {
  stdout: string;
  stderr: string;
  exitCode: number;
}

export interface RuntimeFileStat {
  isFile: boolean;
  isDirectory: boolean;
  isSymbolicLink: boolean;
  size: number;
  mtime: Date;
}

export interface RuntimeScopeOptions {
  cwd?: string;
  commands?: RuntimeCommandGrant[];
}

export interface RuntimeWorkspaceEnv {
  readonly cwd: string;

  exec(command: string, options?: RuntimeExecOptions): Promise<RuntimeExecResult>;
  scope(options?: RuntimeScopeOptions): Promise<RuntimeWorkspaceEnv>;

  readFile(filePath: string): Promise<string>;
  readFileBytes(filePath: string): Promise<Uint8Array>;
  writeFile(filePath: string, content: string | Uint8Array): Promise<void>;
  stat(filePath: string): Promise<RuntimeFileStat>;
  readdir(dirPath: string): Promise<string[]>;
  exists(filePath: string): Promise<boolean>;
  mkdir(dirPath: string, options?: { recursive?: boolean }): Promise<void>;
  rm(filePath: string, options?: { recursive?: boolean; force?: boolean }): Promise<void>;

  resolvePath(filePath: string): string;
  cleanup(): Promise<void>;
}

export interface InMemoryWorkspaceEnvOptions {
  cwd?: string;
  files?: Record<string, string | Uint8Array>;
}

export interface LocalWorkspaceEnvOptions {
  root: string;
  cwd?: string;
}

export interface RuntimeCommandContext {
  cwd: string;
  env: Record<string, string>;
  signal?: AbortSignal;
}

export type RuntimeCommandHandler = (
  args: string[],
  context: RuntimeCommandContext,
) => Promise<RuntimeExecResult> | RuntimeExecResult;

export interface RuntimeCommandGrantOptions {
  env?: Record<string, string>;
}

export interface RuntimeCommandGrant {
  name: string;
  env: Record<string, string>;
  execute: RuntimeCommandHandler;
}

type MemoryFile = {
  content: Uint8Array;
  mtime: Date;
};

type MemoryState = {
  files: Map<string, MemoryFile>;
  dirs: Map<string, Date>;
};

export function createInMemoryWorkspaceEnv(
  options: InMemoryWorkspaceEnvOptions = {},
): RuntimeWorkspaceEnv {
  return new InMemoryWorkspaceEnv(createMemoryState(options.files), options.cwd ?? "/");
}

export function createLocalWorkspaceEnv(options: LocalWorkspaceEnvOptions): RuntimeWorkspaceEnv {
  return new LocalWorkspaceEnv(options.root, options.cwd ?? "/");
}

export function defineRuntimeCommand(
  name: string,
  execute: RuntimeCommandHandler,
  options: RuntimeCommandGrantOptions = {},
): RuntimeCommandGrant {
  const trimmed = name.trim();
  if (!trimmed || /\s/.test(trimmed)) {
    throw new Error("Runtime command names must be non-empty and contain no whitespace");
  }
  return {
    name: trimmed,
    env: { ...(options.env ?? {}) },
    execute,
  };
}

function createMemoryState(files?: Record<string, string | Uint8Array>): MemoryState {
  const state: MemoryState = {
    files: new Map(),
    dirs: new Map([["/", new Date()]]),
  };
  for (const [filePath, content] of Object.entries(files ?? {})) {
    const resolved = normalizeVirtualPath(filePath, "/");
    ensureMemoryParentDirs(state, path.posix.dirname(resolved));
    state.files.set(resolved, {
      content: toBytes(content),
      mtime: new Date(),
    });
  }
  return state;
}

function normalizeVirtualPath(filePath: string, cwd: string): string {
  const base = cwd.startsWith("/") ? cwd : `/${cwd}`;
  const raw = filePath.startsWith("/")
    ? filePath
    : path.posix.join(base, filePath || ".");
  const normalized = path.posix.normalize(raw);
  const absolute = normalized.startsWith("/") ? normalized : `/${normalized}`;
  return absolute.length > 1 && absolute.endsWith("/")
    ? absolute.slice(0, -1)
    : absolute;
}

function toBytes(content: string | Uint8Array): Uint8Array {
  if (typeof content === "string") return Buffer.from(content, "utf-8");
  return new Uint8Array(content);
}

function bytesToString(content: Uint8Array): string {
  return Buffer.from(content).toString("utf-8");
}

function copyBytes(content: Uint8Array): Uint8Array {
  return new Uint8Array(content);
}

function ensureMemoryParentDirs(state: MemoryState, dirPath: string): void {
  let current = "/";
  state.dirs.set(current, state.dirs.get(current) ?? new Date());
  for (const part of dirPath.split("/").filter(Boolean)) {
    current = current === "/" ? `/${part}` : `${current}/${part}`;
    state.dirs.set(current, state.dirs.get(current) ?? new Date());
  }
}

function memoryFileStat(file: MemoryFile): RuntimeFileStat {
  return {
    isFile: true,
    isDirectory: false,
    isSymbolicLink: false,
    size: file.content.byteLength,
    mtime: file.mtime,
  };
}

function memoryDirStat(mtime: Date): RuntimeFileStat {
  return {
    isFile: false,
    isDirectory: true,
    isSymbolicLink: false,
    size: 0,
    mtime,
  };
}

class InMemoryWorkspaceEnv implements RuntimeWorkspaceEnv {
  readonly cwd: string;
  #closed = false;
  #commands: Map<string, RuntimeCommandGrant>;
  #state: MemoryState;

  constructor(state: MemoryState, cwd: string, commands: RuntimeCommandGrant[] = []) {
    this.#state = state;
    this.cwd = normalizeVirtualPath(cwd, "/");
    this.#commands = commandMap(commands);
    ensureMemoryParentDirs(this.#state, this.cwd);
  }

  async exec(command: string, options: RuntimeExecOptions = {}): Promise<RuntimeExecResult> {
    this.#assertOpen();
    const granted = await maybeRunGrantedCommand(
      this.#commands,
      command,
      options,
      options.cwd ? this.resolvePath(options.cwd) : this.cwd,
    );
    if (granted) return granted;
    return {
      stdout: "",
      stderr: `In-memory workspace does not provide shell execution: ${command}`,
      exitCode: 127,
    };
  }

  async scope(options: RuntimeScopeOptions = {}): Promise<RuntimeWorkspaceEnv> {
    this.#assertOpen();
    return new InMemoryWorkspaceEnv(
      this.#state,
      options.cwd ? this.resolvePath(options.cwd) : this.cwd,
      mergeCommandGrants([...this.#commands.values()], options.commands ?? []),
    );
  }

  async readFile(filePath: string): Promise<string> {
    return bytesToString(await this.readFileBytes(filePath));
  }

  async readFileBytes(filePath: string): Promise<Uint8Array> {
    this.#assertOpen();
    const resolved = this.resolvePath(filePath);
    const file = this.#state.files.get(resolved);
    if (!file) throw new Error(`File not found: ${resolved}`);
    return copyBytes(file.content);
  }

  async writeFile(filePath: string, content: string | Uint8Array): Promise<void> {
    this.#assertOpen();
    const resolved = this.resolvePath(filePath);
    ensureMemoryParentDirs(this.#state, path.posix.dirname(resolved));
    this.#state.files.set(resolved, {
      content: toBytes(content),
      mtime: new Date(),
    });
  }

  async stat(filePath: string): Promise<RuntimeFileStat> {
    this.#assertOpen();
    const resolved = this.resolvePath(filePath);
    const file = this.#state.files.get(resolved);
    if (file) return memoryFileStat(file);
    const dirMtime = this.#state.dirs.get(resolved);
    if (dirMtime) return memoryDirStat(dirMtime);
    throw new Error(`Path not found: ${resolved}`);
  }

  async readdir(dirPath: string): Promise<string[]> {
    this.#assertOpen();
    const resolved = this.resolvePath(dirPath);
    if (!this.#state.dirs.has(resolved)) throw new Error(`Directory not found: ${resolved}`);
    const entries = new Set<string>();
    for (const candidate of [...this.#state.dirs.keys(), ...this.#state.files.keys()]) {
      if (candidate === resolved) continue;
      if (path.posix.dirname(candidate) === resolved) {
        entries.add(path.posix.basename(candidate));
      }
    }
    return [...entries].sort();
  }

  async exists(filePath: string): Promise<boolean> {
    this.#assertOpen();
    const resolved = this.resolvePath(filePath);
    return this.#state.files.has(resolved) || this.#state.dirs.has(resolved);
  }

  async mkdir(dirPath: string, options: { recursive?: boolean } = {}): Promise<void> {
    this.#assertOpen();
    const resolved = this.resolvePath(dirPath);
    const parent = path.posix.dirname(resolved);
    if (!options.recursive && !this.#state.dirs.has(parent)) {
      throw new Error(`Parent directory not found: ${parent}`);
    }
    ensureMemoryParentDirs(this.#state, options.recursive ? resolved : parent);
    this.#state.dirs.set(resolved, new Date());
  }

  async rm(filePath: string, options: { recursive?: boolean; force?: boolean } = {}): Promise<void> {
    this.#assertOpen();
    const resolved = this.resolvePath(filePath);
    if (this.#state.files.delete(resolved)) return;
    if (!this.#state.dirs.has(resolved)) {
      if (options.force) return;
      throw new Error(`Path not found: ${resolved}`);
    }
    const children = [...this.#state.files.keys(), ...this.#state.dirs.keys()].filter(
      (candidate) => candidate !== resolved && candidate.startsWith(`${resolved}/`),
    );
    if (children.length > 0 && !options.recursive) {
      throw new Error(`Directory not empty: ${resolved}`);
    }
    for (const child of children) {
      this.#state.files.delete(child);
      this.#state.dirs.delete(child);
    }
    if (resolved !== "/") this.#state.dirs.delete(resolved);
  }

  resolvePath(filePath: string): string {
    return normalizeVirtualPath(filePath, this.cwd);
  }

  async cleanup(): Promise<void> {
    this.#closed = true;
  }

  #assertOpen(): void {
    if (this.#closed) throw new Error("Workspace environment has been cleaned up");
  }
}

class LocalWorkspaceEnv implements RuntimeWorkspaceEnv {
  readonly cwd: string;
  #root: string;
  #commands: Map<string, RuntimeCommandGrant>;

  constructor(root: string, cwd: string, commands: RuntimeCommandGrant[] = []) {
    this.#root = path.resolve(root);
    this.cwd = normalizeVirtualPath(cwd, "/");
    this.#commands = commandMap(commands);
  }

  async exec(command: string, options: RuntimeExecOptions = {}): Promise<RuntimeExecResult> {
    if (options.signal?.aborted) {
      return { stdout: "", stderr: "Operation aborted", exitCode: 130 };
    }
    const virtualCwd = options.cwd ? this.resolvePath(options.cwd) : this.cwd;
    const granted = await maybeRunGrantedCommand(this.#commands, command, options, virtualCwd);
    if (granted) return granted;
    return runShell(command, this.#toHostPath(virtualCwd), options);
  }

  async scope(options: RuntimeScopeOptions = {}): Promise<RuntimeWorkspaceEnv> {
    return new LocalWorkspaceEnv(
      this.#root,
      options.cwd ? this.resolvePath(options.cwd) : this.cwd,
      mergeCommandGrants([...this.#commands.values()], options.commands ?? []),
    );
  }

  async readFile(filePath: string): Promise<string> {
    return fs.readFile(this.#toHostPath(this.resolvePath(filePath)), "utf-8");
  }

  async readFileBytes(filePath: string): Promise<Uint8Array> {
    const content = await fs.readFile(this.#toHostPath(this.resolvePath(filePath)));
    return new Uint8Array(content);
  }

  async writeFile(filePath: string, content: string | Uint8Array): Promise<void> {
    const hostPath = this.#toHostPath(this.resolvePath(filePath));
    await fs.mkdir(path.dirname(hostPath), { recursive: true });
    await fs.writeFile(hostPath, content);
  }

  async stat(filePath: string): Promise<RuntimeFileStat> {
    const stat = await fs.lstat(this.#toHostPath(this.resolvePath(filePath)));
    return {
      isFile: stat.isFile(),
      isDirectory: stat.isDirectory(),
      isSymbolicLink: stat.isSymbolicLink(),
      size: stat.size,
      mtime: stat.mtime,
    };
  }

  async readdir(dirPath: string): Promise<string[]> {
    return (await fs.readdir(this.#toHostPath(this.resolvePath(dirPath)))).sort();
  }

  async exists(filePath: string): Promise<boolean> {
    try {
      await fs.access(this.#toHostPath(this.resolvePath(filePath)));
      return true;
    } catch {
      return false;
    }
  }

  async mkdir(dirPath: string, options: { recursive?: boolean } = {}): Promise<void> {
    await fs.mkdir(this.#toHostPath(this.resolvePath(dirPath)), {
      recursive: options.recursive ?? false,
    });
  }

  async rm(filePath: string, options: { recursive?: boolean; force?: boolean } = {}): Promise<void> {
    await fs.rm(this.#toHostPath(this.resolvePath(filePath)), {
      recursive: options.recursive ?? false,
      force: options.force ?? false,
    });
  }

  resolvePath(filePath: string): string {
    return normalizeVirtualPath(filePath, this.cwd);
  }

  async cleanup(): Promise<void> {
    // Local workspaces are caller-owned. Cleanup is intentionally a no-op.
  }

  #toHostPath(virtualPath: string): string {
    const relative = virtualPath.replace(/^\/+/, "");
    const hostPath = path.resolve(this.#root, relative);
    const outsideRoot = path.relative(this.#root, hostPath).startsWith("..");
    if (outsideRoot) throw new Error(`Path escapes workspace root: ${virtualPath}`);
    return hostPath;
  }
}

function commandMap(commands: RuntimeCommandGrant[]): Map<string, RuntimeCommandGrant> {
  const result = new Map<string, RuntimeCommandGrant>();
  for (const command of commands) {
    result.set(command.name, command);
  }
  return result;
}

function mergeCommandGrants(
  base: RuntimeCommandGrant[],
  overrides: RuntimeCommandGrant[],
): RuntimeCommandGrant[] {
  const result = commandMap(base);
  for (const command of overrides) {
    result.set(command.name, command);
  }
  return [...result.values()];
}

async function maybeRunGrantedCommand(
  commands: Map<string, RuntimeCommandGrant>,
  commandLine: string,
  options: RuntimeExecOptions,
  cwd: string,
): Promise<RuntimeExecResult | null> {
  const parsed = parseCommandLine(commandLine);
  if (!parsed) return null;
  const grant = commands.get(parsed.name);
  if (!grant) return null;
  return grant.execute(parsed.args, {
    cwd,
    env: { ...(options.env ?? {}), ...grant.env },
    signal: options.signal,
  });
}

function parseCommandLine(commandLine: string): { name: string; args: string[] } | null {
  const tokens = commandLine.match(/(?:[^\s"']+|"[^"]*"|'[^']*')+/g);
  if (!tokens || tokens.length === 0) return null;
  const [name, ...args] = tokens.map((token) => stripMatchingQuotes(token));
  if (!name) return null;
  return { name, args };
}

function stripMatchingQuotes(token: string): string {
  if (token.length >= 2 && token[0] === token[token.length - 1] && (token[0] === '"' || token[0] === "'")) {
    return token.slice(1, -1);
  }
  return token;
}

function runShell(
  command: string,
  cwd: string,
  options: RuntimeExecOptions,
): Promise<RuntimeExecResult> {
  return new Promise((resolve) => {
    let stdout = "";
    let stderr = "";
    let timedOut = false;

    const child = spawn(command, {
      cwd,
      env: { ...process.env, ...(options.env ?? {}) },
      shell: true,
      stdio: ["ignore", "pipe", "pipe"],
    });

    const timeout = options.timeoutMs
      ? setTimeout(() => {
          timedOut = true;
          child.kill("SIGTERM");
        }, options.timeoutMs)
      : undefined;

    const abort = () => {
      child.kill("SIGTERM");
    };
    options.signal?.addEventListener("abort", abort, { once: true });

    child.stdout?.on("data", (chunk) => {
      stdout += String(chunk);
    });
    child.stderr?.on("data", (chunk) => {
      stderr += String(chunk);
    });
    child.on("error", (error) => {
      if (timeout) clearTimeout(timeout);
      options.signal?.removeEventListener("abort", abort);
      resolve({ stdout, stderr: stderr || error.message, exitCode: 1 });
    });
    child.on("close", (code) => {
      if (timeout) clearTimeout(timeout);
      options.signal?.removeEventListener("abort", abort);
      if (timedOut) {
        resolve({ stdout, stderr: stderr || "Command timed out", exitCode: 124 });
        return;
      }
      if (options.signal?.aborted) {
        resolve({ stdout, stderr: stderr || "Operation aborted", exitCode: 130 });
        return;
      }
      resolve({ stdout, stderr, exitCode: code ?? 1 });
    });
  });
}
