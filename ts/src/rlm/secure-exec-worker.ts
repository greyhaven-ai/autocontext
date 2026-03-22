import {
  NodeRuntime,
  createNodeDriver,
  createNodeRuntimeDriverFactory,
} from "secure-exec";
import type { ReplCommand, ReplResult, ReplWorker } from "./types.js";

export interface SecureExecReplWorkerOpts {
  namespace?: Record<string, unknown>;
  maxStdoutChars?: number;
  codeTimeoutMs?: number;
  memoryLimitMb?: number;
}

const IDENTIFIER_PATTERN = /^[A-Za-z_$][A-Za-z0-9_$]*$/;
const RESERVED_BINDINGS = new Set([
  "exports",
  "answer",
  "state",
  "vars",
  "peek",
  "grep",
  "chunkBySize",
  "chunkByHeaders",
]);

function toJsonSafe(value: unknown): unknown {
  if (value == null) return value;
  try {
    return JSON.parse(JSON.stringify(value));
  } catch {
    return null;
  }
}

function buildBindings(namespace: Record<string, unknown>): string {
  return Object.keys(namespace)
    .filter((key) => IDENTIFIER_PATTERN.test(key) && !RESERVED_BINDINGS.has(key))
    .map((key) => `let ${key} = vars[${JSON.stringify(key)}];`)
    .join("\n");
}

function buildPersistence(namespace: Record<string, unknown>): string {
  return Object.keys(namespace)
    .filter((key) => IDENTIFIER_PATTERN.test(key) && !RESERVED_BINDINGS.has(key))
    .map((key) => `vars[${JSON.stringify(key)}] = ${key};`)
    .join("\n");
}

function buildWrappedProgram(command: string, namespace: Record<string, unknown>): string {
  const namespaceJson = JSON.stringify(namespace);
  const bindings = buildBindings(namespace);
  const persistence = buildPersistence(namespace);

  return `
const vars = JSON.parse(${JSON.stringify(namespaceJson)});
const peek = (text, start = 0, length = 2000) => String(text ?? "").slice(start, start + length);
const grep = (text, pattern, context = 0) => {
  const source = String(text ?? "");
  const lines = source.split(/\\r?\\n/);
  const needle = String(pattern ?? "").toLowerCase();
  const hits = [];
  for (let index = 0; index < lines.length; index += 1) {
    if (!lines[index].toLowerCase().includes(needle)) continue;
    const start = Math.max(0, index - context);
    const end = Math.min(lines.length, index + context + 1);
    hits.push(lines.slice(start, end).join("\\n"));
  }
  return hits;
};
const chunkBySize = (text, size = 4000, overlap = 0) => {
  const source = String(text ?? "");
  if (!source) return [];
  if (size <= 0) throw new Error("size must be positive");
  if (overlap < 0 || overlap >= size) throw new Error("overlap must be between 0 and size - 1");
  const chunks = [];
  const step = size - overlap;
  for (let start = 0; start < source.length; start += step) {
    chunks.push(source.slice(start, start + size));
    if (start + size >= source.length) break;
  }
  return chunks;
};
const chunkByHeaders = (text) => {
  const source = String(text ?? "");
  if (!source.trim()) return [];
  const lines = source.split(/\\r?\\n/);
  const sections = [];
  let current = { header: "", content: [] };
  for (const line of lines) {
    if (/^#{1,3}\\s/.test(line)) {
      if (current.header || current.content.length) {
        sections.push({ header: current.header, content: current.content.join("\\n").trim() });
      }
      current = { header: line.trim(), content: [] };
      continue;
    }
    current.content.push(line);
  }
  if (current.header || current.content.length) {
    sections.push({ header: current.header, content: current.content.join("\\n").trim() });
  }
  return sections;
};
let answer =
  typeof vars.answer === "object" && vars.answer !== null
    ? vars.answer
    : { content: "", ready: false };
let state =
  typeof vars.state === "object" && vars.state !== null
    ? vars.state
    : {};
${bindings}
${command}
vars.answer = answer;
vars.state = state;
${persistence}
exports.default = JSON.stringify({
  answer: vars.answer ?? { content: "", ready: false },
  namespace: vars,
});
`;
}

function joinOutput(parts: string[]): string {
  return parts.join("");
}

export class SecureExecReplWorker implements ReplWorker {
  readonly namespace: Record<string, unknown>;

  private readonly runtime: NodeRuntime;
  private readonly maxStdoutChars: number;
  private stdoutParts: string[] = [];
  private stderrParts: string[] = [];

  constructor(opts: SecureExecReplWorkerOpts = {}) {
    this.namespace = {
      answer: { content: "", ready: false },
      state: {},
      ...(toJsonSafe(opts.namespace ?? {}) as Record<string, unknown> | null ?? {}),
    };
    this.maxStdoutChars = opts.maxStdoutChars ?? 8192;
    this.runtime = new NodeRuntime({
      systemDriver: createNodeDriver(),
      runtimeDriverFactory: createNodeRuntimeDriverFactory(),
      memoryLimit: opts.memoryLimitMb ?? 64,
      cpuTimeLimitMs: opts.codeTimeoutMs ?? 10000,
      onStdio: (event) => {
        if (event.channel === "stderr") {
          this.stderrParts.push(event.message);
          return;
        }
        this.stdoutParts.push(event.message);
      },
      resourceBudgets: {
        maxOutputBytes: this.maxStdoutChars * 2,
        maxBridgeCalls: 100,
      },
    });
  }

  async runCode(command: ReplCommand): Promise<ReplResult> {
    this.stdoutParts = [];
    this.stderrParts = [];
    const program = buildWrappedProgram(command.code, this.namespace);

    const result = await this.runtime.run<Record<string, string>>(program, "repl-session.js");
    const stdout = joinOutput(this.stdoutParts).slice(0, this.maxStdoutChars);
    const stderr = joinOutput(this.stderrParts).slice(0, this.maxStdoutChars);

    let answer = this.namespace.answer as Record<string, unknown>;
    if (result.code === 0 && typeof result.exports?.default === "string") {
      try {
        const parsed = JSON.parse(result.exports.default) as {
          answer?: Record<string, unknown>;
          namespace?: Record<string, unknown>;
        };
        if (parsed.namespace && typeof parsed.namespace === "object") {
          const nextNamespace = toJsonSafe(parsed.namespace) as Record<string, unknown> | null;
          if (nextNamespace) {
            Object.keys(this.namespace).forEach((key) => delete this.namespace[key]);
            Object.assign(this.namespace, nextNamespace);
          }
        }
        if (parsed.answer && typeof parsed.answer === "object") {
          answer = parsed.answer;
        }
      } catch {
        // Keep prior namespace and surface the parse failure below.
      }
    }

    const errorParts: string[] = [];
    if (stderr) errorParts.push(stderr);
    if (result.code !== 0 && result.errorMessage) errorParts.push(result.errorMessage);

    return {
      stdout,
      error: errorParts.length > 0 ? errorParts.join("\n") : null,
      answer,
    };
  }

  async dispose(): Promise<void> {
    await this.runtime.terminate();
  }
}
