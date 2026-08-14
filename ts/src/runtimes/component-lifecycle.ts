export const RuntimeComponentLifecycleState = {
  INACTIVE: "inactive",
  LOADING: "loading",
  ACTIVE: "active",
  UNLOADING: "unloading",
  FAILED: "failed",
} as const;

export type RuntimeComponentLifecycleState =
  (typeof RuntimeComponentLifecycleState)[keyof typeof RuntimeComponentLifecycleState];

export type RuntimeComponentLifecycleOperation = "activate" | "dispose" | "unwind";
export type RuntimeComponentLifecycleOutcome = "started" | "succeeded" | "failed";

export interface RuntimeComponentLifecycleEvent {
  readonly componentId: string;
  readonly previousState: RuntimeComponentLifecycleState;
  readonly state: RuntimeComponentLifecycleState;
  readonly operation: RuntimeComponentLifecycleOperation;
  readonly outcome: RuntimeComponentLifecycleOutcome;
}

export interface RuntimeComponentLifecycleEventSink {
  onRuntimeComponentLifecycleEvent(event: RuntimeComponentLifecycleEvent): void;
}

export interface RuntimeComponentScopeOptions {
  componentId: string;
  eventSink?: RuntimeComponentLifecycleEventSink;
}

export type RuntimeComponentDisposer = () => void | Promise<void>;
export type RuntimeOwnedComponentDisposer = () => Promise<void>;
export type RuntimeComponentActivator = (
  scope: RuntimeComponentScope,
) => void | Promise<void>;

interface TrackedDisposer {
  readonly dispose: RuntimeComponentDisposer;
  invoked: boolean;
}

/**
 * Owns the reversible effects created while one runtime component is active.
 *
 * A scope is single-use: activate it once, then dispose it at most once. Each
 * deferred disposer is invoked in reverse registration order and is marked
 * invoked before execution so retries cannot repeat an inverse.
 */
export class RuntimeComponentScope {
  readonly componentId: string;
  private readonly eventSink?: RuntimeComponentLifecycleEventSink;
  private readonly disposers: TrackedDisposer[] = [];
  private currentState: RuntimeComponentLifecycleState = RuntimeComponentLifecycleState.INACTIVE;
  private activationAttempted = false;
  private disposalPromise: Promise<void> | null = null;

  constructor(options: RuntimeComponentScopeOptions) {
    this.componentId = validateComponentId(options.componentId);
    this.eventSink = options.eventSink;
  }

  get state(): RuntimeComponentLifecycleState {
    return this.currentState;
  }

  async activate(activator: RuntimeComponentActivator): Promise<void> {
    if (this.activationAttempted) {
      throw new Error(`runtime component ${this.componentId} has already attempted activation`);
    }
    this.activationAttempted = true;
    this.transition(RuntimeComponentLifecycleState.LOADING, "activate", "started");

    try {
      await activator(this);
      this.transition(RuntimeComponentLifecycleState.ACTIVE, "activate", "succeeded");
    } catch (activationError) {
      this.transition(RuntimeComponentLifecycleState.UNLOADING, "unwind", "started");
      const cleanupErrors = await this.drainDisposers();
      this.transition(RuntimeComponentLifecycleState.FAILED, "activate", "failed");
      if (cleanupErrors.length > 0) {
        throw new AggregateError(
          [activationError, ...cleanupErrors],
          `runtime component ${this.componentId} activation and cleanup failed`,
        );
      }
      throw activationError;
    }
  }

  defer(disposer: RuntimeComponentDisposer): RuntimeOwnedComponentDisposer {
    if (
      this.currentState !== RuntimeComponentLifecycleState.LOADING
      && this.currentState !== RuntimeComponentLifecycleState.ACTIVE
    ) {
      throw new Error(
        `runtime component ${this.componentId} cannot register cleanup while ${this.currentState}`,
      );
    }
    const tracked: TrackedDisposer = { dispose: disposer, invoked: false };
    this.disposers.push(tracked);
    return async () => {
      await invokeDisposer(tracked);
    };
  }

  dispose(): Promise<void> {
    if (this.disposalPromise) {
      return this.disposalPromise;
    }
    if (
      this.currentState === RuntimeComponentLifecycleState.INACTIVE
      || this.currentState === RuntimeComponentLifecycleState.FAILED
    ) {
      return Promise.resolve();
    }
    if (this.currentState !== RuntimeComponentLifecycleState.ACTIVE) {
      return Promise.reject(new Error(
        `runtime component ${this.componentId} cannot dispose while ${this.currentState}`,
      ));
    }

    this.disposalPromise = this.disposeActiveComponent();
    return this.disposalPromise;
  }

  private async disposeActiveComponent(): Promise<void> {
    this.transition(RuntimeComponentLifecycleState.UNLOADING, "dispose", "started");
    const cleanupErrors = await this.drainDisposers();
    if (cleanupErrors.length > 0) {
      this.transition(RuntimeComponentLifecycleState.FAILED, "dispose", "failed");
      throw new AggregateError(
        cleanupErrors,
        `runtime component ${this.componentId} disposal failed`,
      );
    }
    this.transition(RuntimeComponentLifecycleState.INACTIVE, "dispose", "succeeded");
  }

  private async drainDisposers(): Promise<unknown[]> {
    const errors: unknown[] = [];
    while (this.disposers.length > 0) {
      const tracked = this.disposers.pop()!;
      try {
        await invokeDisposer(tracked);
      } catch (error) {
        errors.push(error);
      }
    }
    return errors;
  }

  private transition(
    state: RuntimeComponentLifecycleState,
    operation: RuntimeComponentLifecycleOperation,
    outcome: RuntimeComponentLifecycleOutcome,
  ): void {
    const previousState = this.currentState;
    this.currentState = state;
    try {
      this.eventSink?.onRuntimeComponentLifecycleEvent({
        componentId: this.componentId,
        previousState,
        state,
        operation,
        outcome,
      });
    } catch {
      // Lifecycle observers must not become component-owned failure points.
    }
  }
}

export async function activateRuntimeComponent(
  options: RuntimeComponentScopeOptions,
  activator: RuntimeComponentActivator,
): Promise<RuntimeComponentScope> {
  const scope = new RuntimeComponentScope(options);
  await scope.activate(activator);
  return scope;
}

async function invokeDisposer(tracked: TrackedDisposer): Promise<void> {
  if (tracked.invoked) return;
  tracked.invoked = true;
  await tracked.dispose();
}

function validateComponentId(componentId: string): string {
  const normalized = componentId.trim();
  if (!normalized) {
    throw new Error("runtime component id must be non-empty");
  }
  if (normalized.length > 160) {
    throw new Error("runtime component id must be at most 160 characters");
  }
  if (/[\u0000-\u001f\u007f]/.test(normalized)) {
    throw new Error("runtime component id must not contain control characters");
  }
  return normalized;
}
