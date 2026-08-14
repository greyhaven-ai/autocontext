import type {
  RuntimeComponentScope,
  RuntimeOwnedComponentDisposer,
} from "./component-lifecycle.js";
import type { RuntimeComponentGraphSnapshot } from "./component-graph.js";
import type { RuntimeEffectClass } from "./effect-policy.js";

export type RuntimeCompositionResourceKind =
  | "hook"
  | "tool"
  | "grant"
  | "subscription"
  | "timer"
  | "task"
  | "temporary_resource"
  | "resource";

export interface RuntimeCompositionResourceDescriptor {
  readonly kind: RuntimeCompositionResourceKind;
  readonly resourceId: string;
  readonly effectClass?: RuntimeEffectClass;
  readonly observationalEquivalence?: string;
}

export interface RuntimeCompositionLifecycleError {
  readonly componentId: string;
  readonly code: string;
}

interface RuntimeCompositionRegistration {
  readonly token: number;
  readonly componentId: string;
  readonly descriptor: RuntimeCompositionResourceDescriptor;
}

export interface RuntimeCompositionInventorySnapshot {
  readonly registrations: readonly RuntimeCompositionRegistrationSnapshot[];
  readonly lifecycleErrors: readonly RuntimeCompositionLifecycleError[];
  readonly excludedIrreversibleEffects: number;
}

export interface RuntimeCompositionRegistrationSnapshot {
  readonly componentId: string;
  readonly kind: RuntimeCompositionResourceKind;
  readonly observationalIdentity: string;
}

export interface RuntimeCompositionObservableSnapshot {
  readonly graphRevision: number;
  readonly transitioning: boolean;
  readonly activePointer: string | null;
  readonly activeComponents: readonly string[];
  readonly providers: readonly string[];
  readonly hooks: readonly string[];
  readonly tools: readonly string[];
  readonly grants: readonly string[];
  readonly subscriptions: readonly string[];
  readonly timers: readonly string[];
  readonly tasks: readonly string[];
  readonly temporaryResources: readonly string[];
  readonly ownedResources: readonly string[];
  readonly lifecycleErrors: readonly string[];
  readonly blockedCapabilities: readonly string[];
  readonly excludedIrreversibleEffects: number;
}

export interface RuntimeCompositionSnapshotInput {
  readonly graph: RuntimeComponentGraphSnapshot;
  readonly inventory: RuntimeCompositionInventory;
  readonly activePointer?: string | null;
}

export interface RuntimeCompositionEquivalenceResult {
  readonly equivalent: boolean;
  readonly dynamic: RuntimeCompositionEquivalenceView;
  readonly clean: RuntimeCompositionEquivalenceView;
}

export type RuntimeCompositionEquivalenceView = Omit<
  RuntimeCompositionObservableSnapshot,
  "graphRevision" | "transitioning"
>;

/** Host-owned registry for effects that should participate in confluence checks. */
export class RuntimeCompositionInventory {
  private nextToken = 0;
  private readonly registrations = new Map<number, RuntimeCompositionRegistration>();
  private readonly lifecycleErrors = new Map<string, RuntimeCompositionLifecycleError>();

  own(
    scope: RuntimeComponentScope,
    descriptor: RuntimeCompositionResourceDescriptor,
    disposer?: () => void | Promise<void>,
  ): RuntimeOwnedComponentDisposer {
    const normalized = normalizeDescriptor(descriptor);
    const token = this.nextToken++;
    const owned = scope.defer(async () => {
      try {
        await disposer?.();
      } finally {
        this.registrations.delete(token);
      }
    });
    this.registrations.set(token, {
      token,
      componentId: scope.componentId,
      descriptor: normalized,
    });
    return owned;
  }

  recordLifecycleError(componentId: string, code: string): void {
    const error = {
      componentId: normalizeIdentifier(componentId, "component"),
      code: normalizeIdentifier(code, "lifecycle error"),
    };
    this.lifecycleErrors.set(`${error.componentId}\u0000${error.code}`, error);
  }

  resolveLifecycleError(componentId: string, code: string): void {
    this.lifecycleErrors.delete(`${componentId.trim()}\u0000${code.trim()}`);
  }

  snapshot(): RuntimeCompositionInventorySnapshot {
    const registrations: RuntimeCompositionRegistrationSnapshot[] = [];
    let excludedIrreversibleEffects = 0;
    for (const registration of this.registrations.values()) {
      if (registration.descriptor.effectClass === "irreversible") {
        excludedIrreversibleEffects += 1;
        continue;
      }
      registrations.push({
        componentId: registration.componentId,
        kind: registration.descriptor.kind,
        observationalIdentity: observationalIdentity(registration.descriptor),
      });
    }
    registrations.sort(compareRegistration);
    return {
      registrations,
      lifecycleErrors: [...this.lifecycleErrors.values()].sort((left, right) =>
        left.componentId.localeCompare(right.componentId) || left.code.localeCompare(right.code),
      ),
      excludedIrreversibleEffects,
    };
  }
}

export function captureRuntimeCompositionSnapshot(
  input: RuntimeCompositionSnapshotInput,
): RuntimeCompositionObservableSnapshot {
  const inventory = input.inventory.snapshot();
  const byKind = (kind: RuntimeCompositionResourceKind): string[] => inventory.registrations
    .filter((registration) => registration.kind === kind)
    .map(registrationIdentity);
  const graphErrors = input.graph.components
    .filter((component) => component.state !== "active")
    .map((component) => [
      component.componentId,
      component.reason ?? component.state,
      component.capabilityId ?? "",
    ].filter(Boolean).join(":"));
  const inventoryErrors = inventory.lifecycleErrors.map(
    (error) => `${error.componentId}:${error.code}`,
  );
  return {
    graphRevision: input.graph.revision,
    transitioning: input.graph.transitioning,
    activePointer: input.activePointer ?? null,
    activeComponents: input.graph.components
      .filter((component) => component.state === "active")
      .map((component) => {
        const bindings = Object.entries(component.providerInstanceIds)
          .map(([capability, provider]) => `${capability}=${provider}`)
          .sort()
          .join(",");
        return `${component.componentId}@${component.instanceId}[${bindings}]`;
      })
      .sort(),
    providers: input.graph.providers
      .map((provider) =>
        `${provider.capabilityId}=${provider.componentId}@${provider.instanceId}`,
      )
      .sort(),
    hooks: byKind("hook"),
    tools: byKind("tool"),
    grants: byKind("grant"),
    subscriptions: byKind("subscription"),
    timers: byKind("timer"),
    tasks: byKind("task"),
    temporaryResources: byKind("temporary_resource"),
    ownedResources: byKind("resource"),
    lifecycleErrors: [...graphErrors, ...inventoryErrors].sort(),
    blockedCapabilities: [...input.graph.blockedCapabilities].sort(),
    excludedIrreversibleEffects: inventory.excludedIrreversibleEffects,
  };
}

export function compareRuntimeCompositionSnapshots(
  dynamic: RuntimeCompositionObservableSnapshot,
  clean: RuntimeCompositionObservableSnapshot,
): RuntimeCompositionEquivalenceResult {
  const dynamicView = runtimeCompositionEquivalenceView(dynamic);
  const cleanView = runtimeCompositionEquivalenceView(clean);
  return {
    equivalent: JSON.stringify(dynamicView) === JSON.stringify(cleanView),
    dynamic: dynamicView,
    clean: cleanView,
  };
}

export function assertRuntimeCompositionQuiescent(
  snapshot: RuntimeCompositionObservableSnapshot,
): void {
  if (snapshot.transitioning) throw new Error("runtime composition is still transitioning");
  if (snapshot.lifecycleErrors.length > 0) {
    throw new Error("runtime composition has unresolved lifecycle errors");
  }
  if (snapshot.blockedCapabilities.length > 0) {
    throw new Error("runtime composition has blocked capabilities");
  }
}

export function runtimeCompositionEquivalenceView(
  snapshot: RuntimeCompositionObservableSnapshot,
): RuntimeCompositionEquivalenceView {
  const { graphRevision: _revision, transitioning: _transitioning, ...view } = snapshot;
  return view;
}

function observationalIdentity(descriptor: RuntimeCompositionResourceDescriptor): string {
  if (descriptor.effectClass === "compensatable") {
    return `equivalent:${descriptor.observationalEquivalence}`;
  }
  return descriptor.resourceId;
}

function registrationIdentity(registration: RuntimeCompositionRegistrationSnapshot): string {
  return `${registration.componentId}:${registration.observationalIdentity}`;
}

function compareRegistration(
  left: RuntimeCompositionRegistrationSnapshot,
  right: RuntimeCompositionRegistrationSnapshot,
): number {
  return left.kind.localeCompare(right.kind)
    || left.componentId.localeCompare(right.componentId)
    || left.observationalIdentity.localeCompare(right.observationalIdentity);
}

function normalizeDescriptor(
  descriptor: RuntimeCompositionResourceDescriptor,
): RuntimeCompositionResourceDescriptor {
  const resourceId = normalizeIdentifier(descriptor.resourceId, "resource");
  if (
    descriptor.effectClass === "compensatable"
    && !descriptor.observationalEquivalence?.trim()
  ) {
    throw new Error("compensatable observable resources require equivalence metadata");
  }
  return {
    ...descriptor,
    resourceId,
    observationalEquivalence: descriptor.observationalEquivalence?.trim(),
  };
}

function normalizeIdentifier(value: string, kind: string): string {
  const normalized = value.trim();
  if (!normalized || normalized.length > 200 || /[\u0000-\u001f\u007f]/.test(normalized)) {
    throw new Error(`runtime composition ${kind} id is invalid`);
  }
  return normalized;
}
