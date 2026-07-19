/**
 * Loop controller — pause/resume state machine with Promise-based blocking (AC-342).
 * Mirrors Python's autocontext/harness/core/controller.py.
 */

export interface RunStopProgress {
  completedGenerations: number;
  bestScore?: number;
}

export class RunStopRequestedError extends Error {
  readonly runId: string;
  readonly commandId: string;
  readonly completedGenerations: number;
  readonly bestScore?: number;

  constructor(opts: {
    runId: string;
    commandId: string;
    progress?: RunStopProgress;
  }) {
    super("Run stop requested by operator");
    this.name = "RunStopRequestedError";
    this.runId = opts.runId;
    this.commandId = opts.commandId;
    this.completedGenerations = opts.progress?.completedGenerations ?? 0;
    this.bestScore = opts.progress?.bestScore;
  }

  withProgress(progress: RunStopProgress): RunStopRequestedError {
    const bestScore =
      this.bestScore === undefined
        ? progress.bestScore
        : progress.bestScore === undefined
          ? this.bestScore
          : Math.max(this.bestScore, progress.bestScore);
    return new RunStopRequestedError({
      runId: this.runId,
      commandId: this.commandId,
      progress: {
        completedGenerations: Math.max(
          this.completedGenerations,
          progress.completedGenerations,
        ),
        ...(bestScore === undefined ? {} : { bestScore }),
      },
    });
  }
}

export function isRunStopRequestedError(error: unknown): error is RunStopRequestedError {
  return error instanceof RunStopRequestedError;
}

export class LoopController {
  #paused = false;
  #resumeResolvers: Array<() => void> = [];
  #stopRequest: RunStopRequestedError | null = null;
  #gateOverride: string | null = null;
  #pendingHint: string | null = null;
  #chatQueue: Array<{ role: string; message: string; resolve: (response: string) => void }> = [];
  #pendingChatResolvers: Array<(response: string) => void> = [];

  beginRun(): void {
    this.#stopRequest = null;
  }

  pause(): void {
    if (this.#stopRequest) return;
    this.#paused = true;
  }

  resume(): void {
    this.#paused = false;
    this.#releasePausedWaiters();
  }

  isPaused(): boolean {
    return this.#paused;
  }

  requestStop(runId: string, commandId: string): "already_requested" | "requested" {
    if (this.#stopRequest) return "already_requested";
    this.#stopRequest = new RunStopRequestedError({ runId, commandId });
    this.#paused = false;
    this.#releasePausedWaiters();
    return "requested";
  }

  getStopRequest(): RunStopRequestedError | null {
    return this.#stopRequest;
  }

  isStopRequested(): boolean {
    return this.#stopRequest !== null;
  }

  throwIfStopRequested(progress?: RunStopProgress): void {
    const request = this.#stopRequest;
    if (!request) return;
    throw progress ? request.withProgress(progress) : request;
  }

  async waitAtBoundary(progress?: RunStopProgress): Promise<void> {
    this.throwIfStopRequested(progress);
    if (this.#paused) {
      await new Promise<void>((resolve) => {
        this.#resumeResolvers.push(resolve);
      });
    }
    this.throwIfStopRequested(progress);
  }

  waitIfPaused(): Promise<void> {
    return this.waitAtBoundary();
  }

  setGateOverride(decision: string): void {
    this.#gateOverride = decision;
  }

  takeGateOverride(): string | null {
    const val = this.#gateOverride;
    this.#gateOverride = null;
    return val;
  }

  injectHint(text: string): void {
    this.#pendingHint = text;
  }

  takeHint(): string | null {
    const val = this.#pendingHint;
    this.#pendingHint = null;
    return val;
  }

  submitChat(role: string, message: string): Promise<string> {
    return new Promise<string>((resolve) => {
      this.#chatQueue.push({ role, message, resolve });
    });
  }

  pollChat(): [string, string] | null {
    if (this.#chatQueue.length === 0) return null;
    const entry = this.#chatQueue.shift()!;
    this.#pendingChatResolvers.push(entry.resolve);
    return [entry.role, entry.message];
  }

  respondChat(_role: string, response: string): void {
    if (this.#pendingChatResolvers.length > 0) {
      const resolve = this.#pendingChatResolvers.shift()!;
      resolve(response);
    }
  }

  #releasePausedWaiters(): void {
    const resolvers = this.#resumeResolvers.splice(0);
    for (const resolve of resolvers) {
      resolve();
    }
  }
}
