import type { RuntimeComponentScope } from "../runtimes/component-lifecycle.js";
import type { RuntimeCompositionInventory } from "../runtimes/composition-observability.js";

export enum HookEvents {
  RUN_START = "run_start",
  RUN_END = "run_end",
  GENERATION_START = "generation_start",
  GENERATION_END = "generation_end",
  CONTEXT_COMPONENTS = "context_components",
  CONTEXT = "context",
  BEFORE_COMPACTION = "before_compaction",
  AFTER_COMPACTION = "after_compaction",
  BEFORE_PROVIDER_REQUEST = "before_provider_request",
  AFTER_PROVIDER_RESPONSE = "after_provider_response",
  BEFORE_JUDGE = "before_judge",
  AFTER_JUDGE = "after_judge",
  ARTIFACT_WRITE = "artifact_write",
}

export interface HookResultOptions {
  payload?: Record<string, unknown>;
  metadata?: Record<string, unknown>;
  replacePayload?: boolean;
  block?: boolean;
  reason?: string;
}

export class HookResult {
  readonly payload: Record<string, unknown> | null;
  readonly metadata: Record<string, unknown> | null;
  readonly replacePayload: boolean;
  readonly block: boolean;
  readonly reason: string;

  constructor(opts: HookResultOptions = {}) {
    this.payload = opts.payload ?? null;
    this.metadata = opts.metadata ?? null;
    this.replacePayload = opts.replacePayload ?? false;
    this.block = opts.block ?? false;
    this.reason = opts.reason ?? "";
  }
}

export interface HookError {
  eventName: string;
  handler: string;
  message: string;
}

export class HookEvent {
  readonly name: string;
  payload: Record<string, unknown>;
  metadata: Record<string, unknown>;
  errors: HookError[];
  blocked: boolean;
  blockReason: string;

  constructor(
    name: HookEvents | string,
    payload: Record<string, unknown> = {},
    metadata: Record<string, unknown> = {},
  ) {
    this.name = eventName(name);
    this.payload = { ...payload };
    this.metadata = { ...metadata };
    this.errors = [];
    this.blocked = false;
    this.blockReason = "";
  }

  raiseIfBlocked(): void {
    if (this.blocked) {
      throw eventBlockError(this);
    }
  }
}

export type HookHandler = (event: HookEvent) => HookResult | Record<string, unknown> | undefined | null;
export type HookDisposer = () => void | Promise<void>;

interface HookRegistration {
  readonly handler: HookHandler;
  active: boolean;
}

export function eventName(value: HookEvents | string): string {
  return typeof value === "string" ? value : String(value);
}

export function eventBlockError(event: HookEvent): Error {
  const reason = event.blockReason ? `: ${event.blockReason}` : "";
  return new Error(`extension hook blocked ${event.name}${reason}`);
}

export class HookBus {
  readonly failFast: boolean;
  readonly loadedExtensions: string[];
  private handlers: Map<string, HookRegistration[]>;

  constructor(opts: { failFast?: boolean; loadedExtensions?: string[] } = {}) {
    this.failFast = opts.failFast ?? false;
    this.loadedExtensions = [...(opts.loadedExtensions ?? [])];
    this.handlers = new Map();
  }

  on(name: HookEvents | string, handler: HookHandler): HookHandler {
    this.register(name, handler);
    return handler;
  }

  subscribe(name: HookEvents | string, handler: HookHandler): HookDisposer {
    return this.register(name, handler);
  }

  private register(name: HookEvents | string, handler: HookHandler): HookDisposer {
    const normalized = eventName(name);
    const handlers = this.handlers.get(normalized) ?? [];
    const registration: HookRegistration = { handler, active: true };
    handlers.push(registration);
    this.handlers.set(normalized, handlers);
    return () => {
      if (!registration.active) return;
      registration.active = false;
      const current = this.handlers.get(normalized);
      if (!current) return;
      const index = current.indexOf(registration);
      if (index !== -1) current.splice(index, 1);
      if (current.length === 0) this.handlers.delete(normalized);
    };
  }

  hasHandlers(name: HookEvents | string): boolean {
    const normalized = eventName(name);
    return Boolean(this.handlers.get(normalized)?.length || this.handlers.get("*")?.length);
  }

  emit(
    name: HookEvents | string,
    payload: Record<string, unknown> = {},
    opts: { metadata?: Record<string, unknown> } = {},
  ): HookEvent {
    const normalized = eventName(name);
    const event = new HookEvent(normalized, payload, opts.metadata ?? {});
    const handlers = [
      ...(this.handlers.get(normalized) ?? []),
      ...(this.handlers.get("*") ?? []),
    ];

    for (const registration of handlers) {
      if (!registration.active) continue;
      const handler = registration.handler;
      try {
        const result = handler(event);
        applyHookResult(event, result);
      } catch (error) {
        if (this.failFast) {
          throw error;
        }
        event.errors.push({
          eventName: normalized,
          handler: handlerName(handler),
          message: error instanceof Error ? error.message : String(error),
        });
      }
      if (event.blocked) {
        break;
      }
    }
    return event;
  }
}

export class ExtensionAPI {
  readonly bus: HookBus;
  readonly scope?: RuntimeComponentScope;
  readonly compositionInventory?: RuntimeCompositionInventory;

  constructor(
    bus: HookBus,
    scope?: RuntimeComponentScope,
    compositionInventory?: RuntimeCompositionInventory,
  ) {
    this.bus = bus;
    this.scope = scope;
    this.compositionInventory = compositionInventory;
  }

  on(name: HookEvents | string, handler: HookHandler): HookHandler;
  on(name: HookEvents | string): (handler: HookHandler) => HookHandler;
  on(
    name: HookEvents | string,
    handler?: HookHandler,
  ): HookHandler | ((handler: HookHandler) => HookHandler) {
    if (handler) {
      this.register(name, handler);
      return handler;
    }
    return (actual: HookHandler) => {
      this.register(name, actual);
      return actual;
    };
  }

  emit(
    name: HookEvents | string,
    payload: Record<string, unknown> = {},
    opts: { metadata?: Record<string, unknown> } = {},
  ): HookEvent {
    return this.bus.emit(name, payload, opts);
  }

  private register(name: HookEvents | string, handler: HookHandler): void {
    const unsubscribe = this.bus.subscribe(name, handler);
    if (this.scope) {
      try {
        this.scope.defer(unsubscribe);
        this.compositionInventory?.own(this.scope, {
          kind: "hook",
          resourceId: eventName(name),
          effectClass: "reversible",
        });
      } catch (error) {
        void unsubscribe();
        throw error;
      }
    }
  }
}

function applyHookResult(
  event: HookEvent,
  result: HookResult | Record<string, unknown> | undefined | null,
): void {
  if (result === undefined || result === null) {
    return;
  }
  if (result instanceof HookResult) {
    if (result.payload) {
      if (result.replacePayload) {
        event.payload = { ...result.payload };
      } else {
        event.payload = { ...event.payload, ...result.payload };
      }
    }
    if (result.metadata) {
      event.metadata = { ...event.metadata, ...result.metadata };
    }
    if (result.block) {
      event.blocked = true;
      event.blockReason = result.reason;
    }
    return;
  }
  event.payload = { ...event.payload, ...result };
}

function handlerName(handler: HookHandler): string {
  return handler.name || "anonymous_hook_handler";
}
