import { randomUUID } from "node:crypto";
import { agentOutputMetadata } from "../runtimes/agent-output-metadata.js";
import type { AgentRuntime } from "../runtimes/base.js";
import type { RuntimeCommandGrant, RuntimeWorkspaceEnv } from "../runtimes/workspace-env.js";
import { Coordinator } from "./coordinator.js";
import {
  RuntimeSessionEventLog,
  RuntimeSessionEventStore,
  RuntimeSessionEventType,
} from "./runtime-events.js";
import { jsonSafeRecord } from "./runtime-json.js";
import type { RuntimeSessionEventSink } from "./runtime-session-notifications.js";

export const DEFAULT_CHILD_TASK_MAX_DEPTH = 4;

export interface RuntimeChildTaskHandlerInput {
  taskId: string;
  childSessionId: string;
  parentSessionId: string;
  workerId: string;
  prompt: string;
  role: string;
  cwd: string;
  depth: number;
  maxDepth: number;
  workspace: RuntimeWorkspaceEnv;
  sessionLog: RuntimeSessionEventLog;
}

export interface RuntimeChildTaskHandlerOutput {
  text: string;
  metadata?: Record<string, unknown>;
}

export type RuntimeChildTaskHandler = (
  input: RuntimeChildTaskHandlerInput,
) => Promise<RuntimeChildTaskHandlerOutput> | RuntimeChildTaskHandlerOutput;

export interface RuntimeChildTaskRunnerOpts {
  coordinator: Coordinator;
  parentLog: RuntimeSessionEventLog;
  workspace: RuntimeWorkspaceEnv;
  eventStore?: RuntimeSessionEventStore;
  eventSink?: RuntimeSessionEventSink;
  depth?: number;
  maxDepth?: number;
}

export interface RuntimeChildTaskRunOpts {
  prompt: string;
  role: string;
  taskId?: string;
  cwd?: string;
  commands?: RuntimeCommandGrant[];
  handler: RuntimeChildTaskHandler;
}

export interface RuntimeChildTaskResult {
  taskId: string;
  childSessionId: string;
  parentSessionId: string;
  workerId: string;
  role: string;
  cwd: string;
  text: string;
  isError: boolean;
  error: string;
  depth: number;
  maxDepth: number;
  childSessionLog: RuntimeSessionEventLog;
}

export interface AgentRuntimeChildTaskHandlerOptions {
  system?: string | ((input: RuntimeChildTaskHandlerInput) => string | undefined);
  schema?: Record<string, unknown>;
}

export function createAgentRuntimeChildTaskHandler(
  runtime: AgentRuntime,
  options: AgentRuntimeChildTaskHandlerOptions = {},
): RuntimeChildTaskHandler {
  return async (input) => {
    const output = await runtime.generate({
      prompt: input.prompt,
      system: resolveSystemPrompt(options.system, input),
      schema: options.schema,
    });
    return {
      text: output.text,
      metadata: agentOutputMetadata(runtime.name, output),
    };
  };
}

export class RuntimeChildTaskRunner {
  private readonly coordinator: Coordinator;
  private readonly parentLog: RuntimeSessionEventLog;
  private readonly workspace: RuntimeWorkspaceEnv;
  private readonly eventStore?: RuntimeSessionEventStore;
  private readonly eventSink?: RuntimeSessionEventSink;
  private readonly depth: number;
  private readonly maxDepth: number;

  constructor(opts: RuntimeChildTaskRunnerOpts) {
    this.coordinator = opts.coordinator;
    this.parentLog = opts.parentLog;
    this.workspace = opts.workspace;
    this.eventStore = opts.eventStore;
    this.eventSink = opts.eventSink;
    this.depth = normalizeDepth(opts.depth ?? 0, "depth");
    this.maxDepth = normalizeDepth(opts.maxDepth ?? DEFAULT_CHILD_TASK_MAX_DEPTH, "maxDepth");
  }

  async run(opts: RuntimeChildTaskRunOpts): Promise<RuntimeChildTaskResult> {
    const taskId = opts.taskId ?? randomUUID().slice(0, 12);
    const worker = this.coordinator.delegate(opts.prompt, opts.role);
    this.coordinator.startWorker(worker.workerId);
    const childDepth = this.depth + 1;

    const childWorkspace = await this.workspace.scope({
      cwd: opts.cwd,
      commands: opts.commands,
    });
    const childSessionId = `task:${this.parentLog.sessionId}:${taskId}:${worker.workerId}`;
    const childLog = RuntimeSessionEventLog.create({
      sessionId: childSessionId,
      parentSessionId: this.parentLog.sessionId,
      taskId,
      workerId: worker.workerId,
      metadata: {
        role: opts.role,
        cwd: childWorkspace.cwd,
        depth: childDepth,
        maxDepth: this.maxDepth,
      },
    });
    this.observeChildLog(childLog);

    this.parentLog.append(RuntimeSessionEventType.CHILD_TASK_STARTED, {
      taskId,
      childSessionId,
      workerId: worker.workerId,
      role: opts.role,
      cwd: childWorkspace.cwd,
      depth: childDepth,
      maxDepth: this.maxDepth,
    });
    childLog.append(RuntimeSessionEventType.PROMPT_SUBMITTED, {
      prompt: opts.prompt,
      role: opts.role,
      cwd: childWorkspace.cwd,
      depth: childDepth,
      maxDepth: this.maxDepth,
    });

    if (this.depth >= this.maxDepth) {
      return this.failChildTask({
        taskId,
        childSessionId,
        workerId: worker.workerId,
        role: opts.role,
        cwd: childWorkspace.cwd,
        depth: childDepth,
        childLog,
        message: `Maximum child task depth (${this.maxDepth}) exceeded`,
      });
    }

    try {
      const output = await opts.handler({
        taskId,
        childSessionId,
        parentSessionId: this.parentLog.sessionId,
        workerId: worker.workerId,
        prompt: opts.prompt,
        role: opts.role,
        cwd: childWorkspace.cwd,
        depth: childDepth,
        maxDepth: this.maxDepth,
        workspace: childWorkspace,
        sessionLog: childLog,
      });
      const text = output.text;
      childLog.append(RuntimeSessionEventType.ASSISTANT_MESSAGE, {
        text,
        metadata: jsonSafeRecord(output.metadata),
        depth: childDepth,
        maxDepth: this.maxDepth,
      });
      this.coordinator.completeWorker(worker.workerId, text);
      this.parentLog.append(RuntimeSessionEventType.CHILD_TASK_COMPLETED, {
        taskId,
        childSessionId,
        workerId: worker.workerId,
        role: opts.role,
        cwd: childWorkspace.cwd,
        result: text,
        isError: false,
        depth: childDepth,
        maxDepth: this.maxDepth,
      });
      const result = this.result({
        taskId,
        childSessionId,
        workerId: worker.workerId,
        role: opts.role,
        cwd: childWorkspace.cwd,
        text,
        isError: false,
        error: "",
        depth: childDepth,
        childLog,
      });
      this.persist(childLog);
      return result;
    } catch (error) {
      const message = error instanceof Error ? error.message : String(error);
      return this.failChildTask({
        taskId,
        childSessionId,
        workerId: worker.workerId,
        role: opts.role,
        cwd: childWorkspace.cwd,
        depth: childDepth,
        childLog,
        message,
      });
    }
  }

  private failChildTask(opts: {
    taskId: string;
    childSessionId: string;
    workerId: string;
    role: string;
    cwd: string;
    depth: number;
    childLog: RuntimeSessionEventLog;
    message: string;
  }): RuntimeChildTaskResult {
    this.coordinator.failWorker(opts.workerId, opts.message);
    opts.childLog.append(RuntimeSessionEventType.ASSISTANT_MESSAGE, {
      text: "",
      error: opts.message,
      isError: true,
      depth: opts.depth,
      maxDepth: this.maxDepth,
    });
    this.parentLog.append(RuntimeSessionEventType.CHILD_TASK_COMPLETED, {
      taskId: opts.taskId,
      childSessionId: opts.childSessionId,
      workerId: opts.workerId,
      role: opts.role,
      cwd: opts.cwd,
      result: "",
      error: opts.message,
      isError: true,
      depth: opts.depth,
      maxDepth: this.maxDepth,
    });
    const result = this.result({
      taskId: opts.taskId,
      childSessionId: opts.childSessionId,
      workerId: opts.workerId,
      role: opts.role,
      cwd: opts.cwd,
      text: "",
      isError: true,
      error: opts.message,
      depth: opts.depth,
      childLog: opts.childLog,
    });
    this.persist(opts.childLog);
    return result;
  }

  private result(opts: {
    taskId: string;
    childSessionId: string;
    workerId: string;
    role: string;
    cwd: string;
    text: string;
    isError: boolean;
    error: string;
    depth: number;
    childLog: RuntimeSessionEventLog;
  }): RuntimeChildTaskResult {
    return {
      taskId: opts.taskId,
      childSessionId: opts.childSessionId,
      parentSessionId: this.parentLog.sessionId,
      workerId: opts.workerId,
      role: opts.role,
      cwd: opts.cwd,
      text: opts.text,
      isError: opts.isError,
      error: opts.error,
      depth: opts.depth,
      maxDepth: this.maxDepth,
      childSessionLog: opts.childLog,
    };
  }

  private persist(childLog: RuntimeSessionEventLog): void {
    this.eventStore?.save(this.parentLog);
    this.eventStore?.save(childLog);
  }

  private observeChildLog(childLog: RuntimeSessionEventLog): void {
    if (!this.eventStore && !this.eventSink) return;
    childLog.subscribe((event, currentLog) => {
      this.eventStore?.save(currentLog);
      try {
        this.eventSink?.onRuntimeSessionEvent(event, currentLog);
      } catch {
        // Observability sinks must never interrupt child task execution.
      }
    });
  }
}

function resolveSystemPrompt(
  system: AgentRuntimeChildTaskHandlerOptions["system"],
  input: RuntimeChildTaskHandlerInput,
): string | undefined {
  return typeof system === "function" ? system(input) : system;
}

function normalizeDepth(value: number, name: string): number {
  if (!Number.isInteger(value) || value < 0) {
    throw new Error(`${name} must be a non-negative integer`);
  }
  return value;
}
