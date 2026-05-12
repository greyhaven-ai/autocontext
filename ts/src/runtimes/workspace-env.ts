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
  tools?: RuntimeToolGrant[];
  grantEventSink?: RuntimeGrantEventSink;
  grantInheritance?: RuntimeGrantInheritanceMode;
}

export interface RuntimeWorkspaceEnv {
  readonly cwd: string;
  readonly tools?: readonly RuntimeToolGrant[];

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
  hostCwd?: string;
  env: Record<string, string>;
  timeoutMs?: number;
  signal?: AbortSignal;
}

export type RuntimeCommandHandler = (
  args: string[],
  context: RuntimeCommandContext,
) => Promise<RuntimeExecResult> | RuntimeExecResult;

export interface RuntimeCommandGrantOptions {
  env?: Record<string, string>;
  description?: string;
  provenance?: RuntimeGrantProvenance;
  scope?: RuntimeGrantScopePolicy;
  outputLimitBytes?: number;
}

export interface RuntimeCommandGrant {
  kind?: "command";
  name: string;
  env: Record<string, string>;
  execute: RuntimeCommandHandler;
  description?: string;
  provenance?: RuntimeGrantProvenance;
  scope?: RuntimeGrantScopePolicy;
  outputLimitBytes?: number;
}

export interface LocalRuntimeCommandGrantOptions extends RuntimeCommandGrantOptions {
  args?: string[];
  inheritEnv?: string[];
  timeoutMs?: number;
}

export interface RuntimeToolGrant {
  kind: "tool";
  name: string;
  description?: string;
  inputSchema?: Record<string, unknown>;
  execute?: RuntimeToolHandler;
  provenance?: RuntimeGrantProvenance;
  scope?: RuntimeGrantScopePolicy;
}

export interface RuntimeToolCallContext {
  signal?: AbortSignal;
  timeoutMs?: number;
}

export interface RuntimeToolCallResult {
  text: string;
  isError?: boolean;
  content?: unknown[];
  structuredContent?: Record<string, unknown>;
  metadata?: Record<string, unknown>;
}

export type RuntimeToolHandler = (
  args: Record<string, unknown>,
  context?: RuntimeToolCallContext,
) => Promise<RuntimeToolCallResult> | RuntimeToolCallResult;

export type RuntimeScopedGrant = RuntimeCommandGrant | RuntimeToolGrant;
export type RuntimeGrantKind = "command" | "tool";
export type RuntimeGrantInheritanceMode = "scope" | "child_task";

export interface RuntimeGrantProvenance {
  source?: string;
  description?: string;
}

export interface RuntimeGrantScopePolicy {
  inheritToChildTasks?: boolean;
}

export type RuntimeGrantEventPhase = "start" | "end" | "error";

export interface RuntimeGrantOutputRedactionMetadata {
  redacted: boolean;
  truncated: boolean;
  originalBytes: number;
  emittedBytes: number;
}

export interface RuntimeGrantRedactionMetadata {
  envKeys: string[];
  args: {
    redacted: boolean;
    truncated: boolean;
  };
  stdout?: RuntimeGrantOutputRedactionMetadata;
  stderr?: RuntimeGrantOutputRedactionMetadata;
  error?: RuntimeGrantOutputRedactionMetadata;
}

export interface RuntimeGrantEvent {
  kind: RuntimeGrantKind;
  phase: RuntimeGrantEventPhase;
  name: string;
  cwd: string;
  argsSummary: string[];
  exitCode?: number;
  stdout?: string;
  stderr?: string;
  error?: string;
  redaction: RuntimeGrantRedactionMetadata;
  provenance?: RuntimeGrantProvenance;
}

export interface RuntimeGrantEventSink {
  onRuntimeGrantEvent(event: RuntimeGrantEvent): void;
}

type MemoryFile = {
  content: Uint8Array;
  mtime: Date;
};

type MemoryState = {
  files: Map<string, MemoryFile>;
  dirs: Map<string, Date>;
};

const DEFAULT_RUNTIME_COMMAND_OUTPUT_LIMIT_BYTES = 4096;
const DEFAULT_RUNTIME_COMMAND_ARG_LIMIT = 12;
const DEFAULT_RUNTIME_COMMAND_ARG_BYTES = 160;
const RUNTIME_TOOL_EVENT_SOURCE = Symbol("runtimeToolEventSource");
const runtimeToolSecretValues = new WeakMap<RuntimeToolGrant, string[]>();

type RuntimeToolGrantEventWrapper = RuntimeToolGrant & {
  [RUNTIME_TOOL_EVENT_SOURCE]?: RuntimeToolGrant;
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
    kind: "command",
    name: trimmed,
    env: { ...(options.env ?? {}) },
    execute,
    description: options.description,
    provenance: options.provenance,
    scope: options.scope,
    outputLimitBytes: normalizeOutputLimit(options.outputLimitBytes),
  };
}

export function registerRuntimeToolGrantSecrets(
  tool: RuntimeToolGrant,
  secrets: string[],
): RuntimeToolGrant {
  const redactionSecrets = uniqueSecretValues(secrets);
  if (redactionSecrets.length > 0) {
    runtimeToolSecretValues.set(rawRuntimeToolGrant(tool), redactionSecrets);
  }
  return tool;
}

export function createLocalRuntimeCommandGrant(
  name: string,
  executable: string,
  options: LocalRuntimeCommandGrantOptions = {},
): RuntimeCommandGrant {
  const cleanExecutable = executable.trim();
  if (!cleanExecutable) {
    throw new Error("Local runtime command executable must be non-empty");
  }
  const fixedArgs = [...(options.args ?? [])];
  const inheritedEnv = pickProcessEnv(options.inheritEnv ?? []);
  return defineRuntimeCommand(
    name,
    (args, context) => runProcess(cleanExecutable, [...fixedArgs, ...args], {
      cwd: context.hostCwd ?? context.cwd,
      env: context.env,
      signal: context.signal,
      timeoutMs: combineTimeoutMs(options.timeoutMs, context.timeoutMs),
    }),
    {
      ...options,
      env: { ...inheritedEnv, ...(options.env ?? {}) },
    },
  );
}

function createMemoryState(files?: Record<string, string | Uint8Array>): MemoryState {
  const state: MemoryState = {
    files: new Map(),
    dirs: new Map([["/", new Date()]]),
  };
  for (const [filePath, content] of Object.entries(files ?? {})) {
    const resolved = normalizeVirtualPath(filePath, "/");
    writeMemoryFile(state, resolved, content);
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
    if (state.files.has(current)) {
      throw new Error(`Not a directory: ${current}`);
    }
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

function writeMemoryFile(
  state: MemoryState,
  resolved: string,
  content: string | Uint8Array,
): void {
  if (state.dirs.has(resolved)) {
    throw new Error(`Is a directory: ${resolved}`);
  }
  ensureMemoryParentDirs(state, path.posix.dirname(resolved));
  state.files.set(resolved, {
    content: toBytes(content),
    mtime: new Date(),
  });
}

class InMemoryWorkspaceEnv implements RuntimeWorkspaceEnv {
  readonly cwd: string;
  #closed = false;
  #commands: Map<string, RuntimeCommandGrant>;
  #tools: Map<string, RuntimeToolGrant>;
  #grantEventSink?: RuntimeGrantEventSink;
  #state: MemoryState;

  constructor(
    state: MemoryState,
    cwd: string,
    commands: RuntimeCommandGrant[] = [],
    tools: RuntimeToolGrant[] = [],
    grantEventSink?: RuntimeGrantEventSink,
  ) {
    this.#state = state;
    this.cwd = normalizeVirtualPath(cwd, "/");
    this.#commands = commandMap(commands);
    this.#tools = toolMap(tools);
    this.#grantEventSink = grantEventSink;
    ensureMemoryParentDirs(this.#state, this.cwd);
  }

  get tools(): readonly RuntimeToolGrant[] {
    return runtimeToolsForWorkspace([...this.#tools.values()], this.cwd, this.#grantEventSink);
  }

  async exec(command: string, options: RuntimeExecOptions = {}): Promise<RuntimeExecResult> {
    this.#assertOpen();
    const granted = await maybeRunGrantedCommand(
      this.#commands,
      command,
      options,
      options.cwd ? this.resolvePath(options.cwd) : this.cwd,
      undefined,
      this.#grantEventSink,
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
      mergeCommandGrants(
        inheritedCommandGrants([...this.#commands.values()], options.grantInheritance),
        options.commands ?? [],
      ),
      mergeToolGrants(
        inheritedToolGrants([...this.#tools.values()], options.grantInheritance),
        options.tools ?? [],
      ),
      options.grantEventSink ?? this.#grantEventSink,
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
    writeMemoryFile(this.#state, resolved, content);
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
    if (this.#state.files.has(resolved)) {
      throw new Error(`File exists: ${resolved}`);
    }
    if (this.#state.dirs.has(resolved)) {
      if (options.recursive) return;
      throw new Error(`Directory exists: ${resolved}`);
    }
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
  #tools: Map<string, RuntimeToolGrant>;
  #grantEventSink?: RuntimeGrantEventSink;

  constructor(
    root: string,
    cwd: string,
    commands: RuntimeCommandGrant[] = [],
    tools: RuntimeToolGrant[] = [],
    grantEventSink?: RuntimeGrantEventSink,
  ) {
    this.#root = path.resolve(root);
    this.cwd = normalizeVirtualPath(cwd, "/");
    this.#commands = commandMap(commands);
    this.#tools = toolMap(tools);
    this.#grantEventSink = grantEventSink;
  }

  get tools(): readonly RuntimeToolGrant[] {
    return runtimeToolsForWorkspace([...this.#tools.values()], this.cwd, this.#grantEventSink);
  }

  async exec(command: string, options: RuntimeExecOptions = {}): Promise<RuntimeExecResult> {
    if (options.signal?.aborted) {
      return { stdout: "", stderr: "Operation aborted", exitCode: 130 };
    }
    const virtualCwd = options.cwd ? this.resolvePath(options.cwd) : this.cwd;
    const hostCwd = this.#toHostPath(virtualCwd);
    const granted = await maybeRunGrantedCommand(
      this.#commands,
      command,
      options,
      virtualCwd,
      hostCwd,
      this.#grantEventSink,
    );
    if (granted) return granted;
    return runShell(command, hostCwd, options);
  }

  async scope(options: RuntimeScopeOptions = {}): Promise<RuntimeWorkspaceEnv> {
    return new LocalWorkspaceEnv(
      this.#root,
      options.cwd ? this.resolvePath(options.cwd) : this.cwd,
      mergeCommandGrants(
        inheritedCommandGrants([...this.#commands.values()], options.grantInheritance),
        options.commands ?? [],
      ),
      mergeToolGrants(
        inheritedToolGrants([...this.#tools.values()], options.grantInheritance),
        options.tools ?? [],
      ),
      options.grantEventSink ?? this.#grantEventSink,
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

function toolMap(tools: RuntimeToolGrant[]): Map<string, RuntimeToolGrant> {
  const result = new Map<string, RuntimeToolGrant>();
  for (const tool of tools) {
    const rawTool = rawRuntimeToolGrant(tool);
    result.set(rawTool.name, rawTool);
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

function mergeToolGrants(
  base: RuntimeToolGrant[],
  overrides: RuntimeToolGrant[],
): RuntimeToolGrant[] {
  const result = toolMap(base);
  for (const tool of overrides) {
    const rawTool = rawRuntimeToolGrant(tool);
    result.set(rawTool.name, rawTool);
  }
  return [...result.values()];
}

function inheritedCommandGrants(
  commands: RuntimeCommandGrant[],
  mode: RuntimeGrantInheritanceMode = "scope",
): RuntimeCommandGrant[] {
  if (mode !== "child_task") return commands;
  return commands.filter((command) => command.scope?.inheritToChildTasks !== false);
}

function inheritedToolGrants(
  tools: RuntimeToolGrant[],
  mode: RuntimeGrantInheritanceMode = "scope",
): RuntimeToolGrant[] {
  if (mode !== "child_task") return tools;
  return tools.filter((tool) => tool.scope?.inheritToChildTasks !== false);
}

async function maybeRunGrantedCommand(
  commands: Map<string, RuntimeCommandGrant>,
  commandLine: string,
  options: RuntimeExecOptions,
  cwd: string,
  hostCwd: string | undefined,
  grantEventSink: RuntimeGrantEventSink | undefined,
): Promise<RuntimeExecResult | null> {
  const parsed = parseCommandLine(commandLine);
  if (!parsed) return null;
  const grant = commands.get(parsed.name);
  if (!grant) return null;
  const commandEnv = { ...(options.env ?? {}), ...grant.env };
  const secrets = secretValues(commandEnv);
  const args = summarizeArgs(parsed.args, secrets);
  const redaction = baseGrantRedaction(commandEnv, args);
  emitRuntimeGrantEvent(grantEventSink, {
    kind: "command",
    phase: "start",
    name: grant.name,
    cwd,
    argsSummary: args.summary,
    redaction,
    provenance: grant.provenance,
  });
  try {
    const result = await grant.execute(parsed.args, {
      cwd,
      hostCwd,
      env: commandEnv,
      timeoutMs: options.timeoutMs,
      signal: options.signal,
    });
    const outputLimitBytes = runtimeCommandOutputLimit(grant);
    const stdout = previewText(result.stdout, secrets, outputLimitBytes);
    const stderr = previewText(result.stderr, secrets, outputLimitBytes);
    emitRuntimeGrantEvent(grantEventSink, {
      kind: "command",
      phase: "end",
      name: grant.name,
      cwd,
      argsSummary: args.summary,
      exitCode: result.exitCode,
      stdout: stdout.text,
      stderr: stderr.text,
      redaction: {
        ...redaction,
        stdout: stdout.metadata,
        stderr: stderr.metadata,
      },
      provenance: grant.provenance,
    });
    return result;
  } catch (error) {
    const rawMessage = error instanceof Error ? error.message : String(error);
    const message = previewText(rawMessage, secrets, runtimeCommandOutputLimit(grant));
    emitRuntimeGrantEvent(grantEventSink, {
      kind: "command",
      phase: "error",
      name: grant.name,
      cwd,
      argsSummary: args.summary,
      error: message.text,
      redaction: {
        ...redaction,
        error: message.metadata,
      },
      provenance: grant.provenance,
    });
    throw error;
  }
}

function runtimeToolsForWorkspace(
  tools: RuntimeToolGrant[],
  cwd: string,
  grantEventSink: RuntimeGrantEventSink | undefined,
): RuntimeToolGrant[] {
  if (!grantEventSink) return tools;
  return tools.map((tool) => runtimeToolWithGrantEvents(tool, cwd, grantEventSink));
}

function runtimeToolWithGrantEvents(
  tool: RuntimeToolGrant,
  cwd: string,
  grantEventSink: RuntimeGrantEventSink,
): RuntimeToolGrant {
  const rawTool = rawRuntimeToolGrant(tool);
  if (!rawTool.execute) return rawTool;
  const wrapped: RuntimeToolGrantEventWrapper = {
    ...rawTool,
    execute: (args, context) =>
      executeRuntimeToolWithGrantEvents(rawTool, args, context, cwd, grantEventSink),
  };
  Object.defineProperty(wrapped, RUNTIME_TOOL_EVENT_SOURCE, { value: rawTool });
  return wrapped;
}

async function executeRuntimeToolWithGrantEvents(
  tool: RuntimeToolGrant,
  args: Record<string, unknown>,
  context: RuntimeToolCallContext | undefined,
  cwd: string,
  grantEventSink: RuntimeGrantEventSink,
): Promise<RuntimeToolCallResult> {
  const secrets = runtimeToolRedactionSecrets(tool);
  const argsSummary = summarizeArgs([safeJsonOrString(args)], secrets);
  const redaction = baseGrantRedaction({}, argsSummary);
  emitRuntimeGrantEvent(grantEventSink, {
    kind: "tool",
    phase: "start",
    name: tool.name,
    cwd,
    argsSummary: argsSummary.summary,
    redaction,
    provenance: tool.provenance,
  });
  try {
    const result = await tool.execute!(args, context);
    const stdout = previewText(result.text, secrets, DEFAULT_RUNTIME_COMMAND_OUTPUT_LIMIT_BYTES);
    emitRuntimeGrantEvent(grantEventSink, {
      kind: "tool",
      phase: "end",
      name: tool.name,
      cwd,
      argsSummary: argsSummary.summary,
      exitCode: result.isError ? 1 : 0,
      stdout: stdout.text,
      redaction: {
        ...redaction,
        stdout: stdout.metadata,
      },
      provenance: tool.provenance,
    });
    return result;
  } catch (error) {
    const rawMessage = error instanceof Error ? error.message : String(error);
    const message = previewText(rawMessage, secrets, DEFAULT_RUNTIME_COMMAND_OUTPUT_LIMIT_BYTES);
    emitRuntimeGrantEvent(grantEventSink, {
      kind: "tool",
      phase: "error",
      name: tool.name,
      cwd,
      argsSummary: argsSummary.summary,
      error: message.text,
      redaction: {
        ...redaction,
        error: message.metadata,
      },
      provenance: tool.provenance,
    });
    throw error;
  }
}

function rawRuntimeToolGrant(tool: RuntimeToolGrant): RuntimeToolGrant {
  return (tool as RuntimeToolGrantEventWrapper)[RUNTIME_TOOL_EVENT_SOURCE] ?? tool;
}

function runtimeToolRedactionSecrets(tool: RuntimeToolGrant): string[] {
  return runtimeToolSecretValues.get(rawRuntimeToolGrant(tool)) ?? [];
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
  return runSpawnedProcess({
    command,
    args: [],
    cwd,
    env: { ...process.env, ...(options.env ?? {}) },
    shell: true,
    signal: options.signal,
    timeoutMs: options.timeoutMs,
  });
}

function runProcess(
  executable: string,
  args: string[],
  options: {
    cwd: string;
    env: NodeJS.ProcessEnv;
    timeoutMs?: number;
    signal?: AbortSignal;
  },
): Promise<RuntimeExecResult> {
  return runSpawnedProcess({
    command: executable,
    args,
    cwd: options.cwd,
    env: options.env,
    shell: false,
    signal: options.signal,
    timeoutMs: options.timeoutMs,
  });
}

function runSpawnedProcess(options: {
  command: string;
  args: string[];
  cwd: string;
  env: NodeJS.ProcessEnv;
  shell: boolean;
  timeoutMs?: number;
  signal?: AbortSignal;
}): Promise<RuntimeExecResult> {
  return new Promise((resolve) => {
    let stdout = "";
    let stderr = "";
    let timedOut = false;

    const child = spawn(options.command, options.args, {
      cwd: options.cwd,
      env: options.env,
      shell: options.shell,
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

function normalizeOutputLimit(value: number | undefined): number {
  if (value === undefined) return DEFAULT_RUNTIME_COMMAND_OUTPUT_LIMIT_BYTES;
  if (!Number.isFinite(value) || value < 0) {
    throw new Error("Runtime command outputLimitBytes must be a non-negative finite number");
  }
  return Math.floor(value);
}

function runtimeCommandOutputLimit(grant: RuntimeCommandGrant): number {
  return normalizeOutputLimit(grant.outputLimitBytes);
}

function secretValues(env: Record<string, string>): string[] {
  return uniqueSecretValues(Object.values(env));
}

function uniqueSecretValues(values: readonly string[]): string[] {
  return [...new Set(values.filter((value) => value.length > 0))].sort(
    (left, right) => right.length - left.length,
  );
}

function baseGrantRedaction(
  env: Record<string, string>,
  args: { redacted: boolean; truncated: boolean },
): RuntimeGrantRedactionMetadata {
  return {
    envKeys: Object.keys(env).sort(),
    args: {
      redacted: args.redacted,
      truncated: args.truncated,
    },
  };
}

function pickProcessEnv(keys: string[]): Record<string, string> {
  const picked: Record<string, string> = {};
  for (const key of keys) {
    const value = process.env[key];
    if (value !== undefined) picked[key] = value;
  }
  return picked;
}

function combineTimeoutMs(
  configured: number | undefined,
  callSite: number | undefined,
): number | undefined {
  if (configured === undefined) return callSite;
  if (callSite === undefined) return configured;
  return Math.min(configured, callSite);
}

function summarizeArgs(
  args: string[],
  secrets: string[],
): { summary: string[]; redacted: boolean; truncated: boolean } {
  let redacted = false;
  let truncated = args.length > DEFAULT_RUNTIME_COMMAND_ARG_LIMIT;
  const summary = args.slice(0, DEFAULT_RUNTIME_COMMAND_ARG_LIMIT).map((arg) => {
    const preview = previewText(arg, secrets, DEFAULT_RUNTIME_COMMAND_ARG_BYTES);
    redacted = redacted || preview.metadata.redacted;
    truncated = truncated || preview.metadata.truncated;
    return preview.text;
  });
  if (args.length > DEFAULT_RUNTIME_COMMAND_ARG_LIMIT) {
    summary.push(`[${args.length - DEFAULT_RUNTIME_COMMAND_ARG_LIMIT} more args]`);
  }
  return { summary, redacted, truncated };
}

function previewText(
  value: string,
  secrets: string[],
  limitBytes: number,
): { text: string; metadata: RuntimeGrantOutputRedactionMetadata } {
  const originalBytes = Buffer.byteLength(value, "utf-8");
  const redacted = redactSecrets(value, secrets);
  const truncated = truncateUtf8(redacted.text, limitBytes);
  return {
    text: truncated.text,
    metadata: {
      redacted: redacted.redacted,
      truncated: truncated.truncated,
      originalBytes,
      emittedBytes: Buffer.byteLength(truncated.text, "utf-8"),
    },
  };
}

function redactSecrets(value: string, secrets: string[]): { text: string; redacted: boolean } {
  let text = value;
  let redacted = false;
  for (const secret of secrets) {
    if (!secret || !text.includes(secret)) continue;
    text = text.split(secret).join("[redacted]");
    redacted = true;
  }
  return { text, redacted };
}

function safeJsonOrString(value: unknown): string {
  try {
    const json = JSON.stringify(value, (_key, candidate) => {
      if (typeof candidate === "bigint") return candidate.toString();
      if (typeof candidate === "symbol") return String(candidate);
      if (typeof candidate === "function") {
        return `[Function ${candidate.name || "anonymous"}]`;
      }
      return candidate;
    });
    if (json !== undefined) return json;
  } catch {
    // Fall through to string coercion.
  }
  try {
    return String(value);
  } catch {
    return "[unserializable]";
  }
}

function truncateUtf8(value: string, limitBytes: number): { text: string; truncated: boolean } {
  const buffer = Buffer.from(value, "utf-8");
  if (buffer.byteLength <= limitBytes) return { text: value, truncated: false };
  return {
    text: buffer.subarray(0, limitBytes).toString("utf-8"),
    truncated: true,
  };
}

function emitRuntimeGrantEvent(
  sink: RuntimeGrantEventSink | undefined,
  event: RuntimeGrantEvent,
): void {
  try {
    sink?.onRuntimeGrantEvent(event);
  } catch {
    // Observability sinks must never change command execution semantics.
  }
}
