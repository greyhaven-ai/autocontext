import { randomUUID } from "node:crypto";
import type { RuntimeCommandGrant, RuntimeWorkspaceEnv } from "../runtimes/workspace-env.js";
import { Coordinator } from "./coordinator.js";
import {
  RuntimeChildTaskRunner,
  type RuntimeChildTaskResult,
  type RuntimeChildTaskRunOpts,
} from "./runtime-child-tasks.js";
import {
  RuntimeSessionEventLog,
  RuntimeSessionEventStore,
  RuntimeSessionEventType,
} from "./runtime-events.js";
import type { RuntimeSessionEventSink } from "./runtime-session-notifications.js";

export interface RuntimeSessionCreateOpts {
  sessionId?: string;
  goal: string;
  workspace: RuntimeWorkspaceEnv;
  eventStore?: RuntimeSessionEventStore;
  eventSink?: RuntimeSessionEventSink;
  metadata?: Record<string, unknown>;
  depth?: number;
  maxDepth?: number;
}

export interface RuntimeSessionLoadOpts {
  sessionId: string;
  workspace: RuntimeWorkspaceEnv;
  eventStore: RuntimeSessionEventStore;
  eventSink?: RuntimeSessionEventSink;
  depth?: number;
  maxDepth?: number;
}

export interface RuntimeSessionPromptHandlerInput {
  sessionId: string;
  prompt: string;
  role: string;
  cwd: string;
  workspace: RuntimeWorkspaceEnv;
  sessionLog: RuntimeSessionEventLog;
}

export interface RuntimeSessionPromptHandlerOutput {
  text: string;
  metadata?: Record<string, unknown>;
}

export type RuntimeSessionPromptHandler = (
  input: RuntimeSessionPromptHandlerInput,
) => Promise<RuntimeSessionPromptHandlerOutput> | RuntimeSessionPromptHandlerOutput;

export interface RuntimeSessionSubmitPromptOpts {
  prompt: string;
  role?: string;
  cwd?: string;
  commands?: RuntimeCommandGrant[];
  handler: RuntimeSessionPromptHandler;
}

export interface RuntimeSessionPromptResult {
  sessionId: string;
  role: string;
  cwd: string;
  text: string;
  isError: boolean;
  error: string;
  sessionLog: RuntimeSessionEventLog;
}

interface RuntimeSessionConstructorOpts {
  goal: string;
  workspace: RuntimeWorkspaceEnv;
  log: RuntimeSessionEventLog;
  coordinator: Coordinator;
  eventStore?: RuntimeSessionEventStore;
  eventSink?: RuntimeSessionEventSink;
  depth?: number;
  maxDepth?: number;
}

export class RuntimeSession {
  readonly goal: string;
  readonly workspace: RuntimeWorkspaceEnv;
  readonly log: RuntimeSessionEventLog;
  readonly coordinator: Coordinator;

  private readonly eventStore?: RuntimeSessionEventStore;
  private readonly eventSink?: RuntimeSessionEventSink;
  private readonly depth?: number;
  private readonly maxDepth?: number;

  private constructor(opts: RuntimeSessionConstructorOpts) {
    this.goal = opts.goal;
    this.workspace = opts.workspace;
    this.log = opts.log;
    this.coordinator = opts.coordinator;
    this.eventStore = opts.eventStore;
    this.eventSink = opts.eventSink;
    this.depth = opts.depth;
    this.maxDepth = opts.maxDepth;
    observeRuntimeSessionLog(this.log, this.eventStore, this.eventSink);
  }

  static create(opts: RuntimeSessionCreateOpts): RuntimeSession {
    const sessionId = opts.sessionId ?? `runtime:${randomUUID().slice(0, 12)}`;
    const metadata = { ...(opts.metadata ?? {}), goal: opts.goal };
    const log = RuntimeSessionEventLog.create({ sessionId, metadata });
    return new RuntimeSession({
      goal: opts.goal,
      workspace: opts.workspace,
      log,
      coordinator: Coordinator.create(sessionId, opts.goal),
      eventStore: opts.eventStore,
      eventSink: opts.eventSink,
      depth: opts.depth,
      maxDepth: opts.maxDepth,
    });
  }

  static load(opts: RuntimeSessionLoadOpts): RuntimeSession | null {
    const log = opts.eventStore.load(opts.sessionId);
    if (!log) return null;
    const goal = readString(log.metadata.goal);
    return new RuntimeSession({
      goal,
      workspace: opts.workspace,
      log,
      coordinator: Coordinator.create(log.sessionId, goal),
      eventStore: opts.eventStore,
      eventSink: opts.eventSink,
      depth: opts.depth,
      maxDepth: opts.maxDepth,
    });
  }

  get sessionId(): string {
    return this.log.sessionId;
  }

  async submitPrompt(opts: RuntimeSessionSubmitPromptOpts): Promise<RuntimeSessionPromptResult> {
    const role = opts.role ?? "assistant";
    const scopedWorkspace = await this.workspace.scope({
      cwd: opts.cwd,
      commands: opts.commands,
    });
    this.log.append(RuntimeSessionEventType.PROMPT_SUBMITTED, {
      prompt: opts.prompt,
      role,
      cwd: scopedWorkspace.cwd,
    });

    try {
      const output = await opts.handler({
        sessionId: this.sessionId,
        prompt: opts.prompt,
        role,
        cwd: scopedWorkspace.cwd,
        workspace: scopedWorkspace,
        sessionLog: this.log,
      });
      this.log.append(RuntimeSessionEventType.ASSISTANT_MESSAGE, {
        text: output.text,
        metadata: output.metadata ?? {},
        role,
        cwd: scopedWorkspace.cwd,
      });
      const result = this.promptResult({
        role,
        cwd: scopedWorkspace.cwd,
        text: output.text,
        isError: false,
        error: "",
      });
      this.save();
      return result;
    } catch (error) {
      const message = error instanceof Error ? error.message : String(error);
      this.log.append(RuntimeSessionEventType.ASSISTANT_MESSAGE, {
        text: "",
        error: message,
        isError: true,
        role,
        cwd: scopedWorkspace.cwd,
      });
      const result = this.promptResult({
        role,
        cwd: scopedWorkspace.cwd,
        text: "",
        isError: true,
        error: message,
      });
      this.save();
      return result;
    }
  }

  async runChildTask(opts: RuntimeChildTaskRunOpts): Promise<RuntimeChildTaskResult> {
    return this.childTaskRunner().run(opts);
  }

  listChildLogs(): RuntimeSessionEventLog[] {
    return this.eventStore?.listChildren(this.sessionId) ?? [];
  }

  save(): void {
    this.eventStore?.save(this.log);
  }

  private childTaskRunner(): RuntimeChildTaskRunner {
    return new RuntimeChildTaskRunner({
      coordinator: this.coordinator,
      parentLog: this.log,
      workspace: this.workspace,
      eventStore: this.eventStore,
      eventSink: this.eventSink,
      depth: this.depth,
      maxDepth: this.maxDepth,
    });
  }

  private promptResult(opts: {
    role: string;
    cwd: string;
    text: string;
    isError: boolean;
    error: string;
  }): RuntimeSessionPromptResult {
    return {
      sessionId: this.sessionId,
      role: opts.role,
      cwd: opts.cwd,
      text: opts.text,
      isError: opts.isError,
      error: opts.error,
      sessionLog: this.log,
    };
  }
}

function readString(value: unknown): string {
  return typeof value === "string" ? value : "";
}

function observeRuntimeSessionLog(
  log: RuntimeSessionEventLog,
  eventStore: RuntimeSessionEventStore | undefined,
  eventSink: RuntimeSessionEventSink | undefined,
): void {
  if (!eventStore && !eventSink) return;
  log.subscribe((event, currentLog) => {
    eventStore?.save(currentLog);
    try {
      eventSink?.onRuntimeSessionEvent(event, currentLog);
    } catch {
      // Observability sinks must never interrupt the runtime session.
    }
  });
}
