import {
  RuntimeComponentScope,
  type RuntimeComponentActivator,
  type RuntimeComponentLifecycleEventSink,
} from "./component-lifecycle.js";

export interface RuntimeCapabilityKey<T> {
  readonly id: string;
  /** Compile-time-only carrier for the capability value type. */
  readonly valueType?: T;
}

export interface RuntimeCapabilityProvision<T> {
  readonly key: RuntimeCapabilityKey<T>;
  readonly value: T;
}

export interface RuntimeComponentManifest {
  /** Stable logical component id. */
  readonly id: string;
  /** Stable identity for this concrete provider/component instance. */
  readonly instanceId: string;
  readonly requires?: readonly RuntimeCapabilityKey<unknown>[];
  readonly provides?: readonly RuntimeCapabilityProvision<unknown>[];
  readonly activate: RuntimeComponentActivatorWithContext;
}

export interface RuntimeComponentActivationContext {
  readonly componentId: string;
  readonly instanceId: string;
  readonly revision: number;
  readonly scope: RuntimeComponentScope;
  get<T>(key: RuntimeCapabilityKey<T>): T;
  providerIdentity<T>(key: RuntimeCapabilityKey<T>): string;
}

export type RuntimeComponentActivatorWithContext = (
  context: RuntimeComponentActivationContext,
) => void | Promise<void>;

export type RuntimeComponentGraphOperation =
  | "provider_unavailable"
  | "component_waiting"
  | "component_activated"
  | "component_deactivated"
  | "component_failed"
  | "graph_reconciled";

export type RuntimeComponentGraphOutcome =
  | "started"
  | "succeeded"
  | "failed"
  | "waiting";

export interface RuntimeComponentGraphEvent {
  readonly revision: number;
  readonly operation: RuntimeComponentGraphOperation;
  readonly outcome: RuntimeComponentGraphOutcome;
  readonly componentId?: string;
  readonly instanceId?: string;
  readonly capabilityId?: string;
  readonly providerComponentId?: string;
  readonly providerInstanceId?: string;
  readonly reason?: RuntimeComponentGraphReason;
}

export interface RuntimeComponentGraphEventSink {
  onRuntimeComponentGraphEvent(event: RuntimeComponentGraphEvent): void;
}

export type RuntimeComponentGraphReason =
  | "activation_failed"
  | "cleanup_failed"
  | "missing_requirement"
  | "provider_cleanup_failed"
  | "provider_inactive";

export type RuntimeComponentGraphComponentState = "active" | "waiting" | "failed";

export interface RuntimeComponentGraphComponentSnapshot {
  readonly componentId: string;
  readonly instanceId: string;
  readonly state: RuntimeComponentGraphComponentState;
  readonly reason?: RuntimeComponentGraphReason;
  readonly capabilityId?: string;
  readonly providerComponentId?: string;
  readonly providerInstanceIds: Readonly<Record<string, string>>;
  readonly requires: readonly string[];
  readonly provides: readonly string[];
}

export interface RuntimeComponentGraphProviderSnapshot {
  readonly capabilityId: string;
  readonly componentId: string;
  readonly instanceId: string;
  readonly available: true;
}

export interface RuntimeComponentGraphSnapshot {
  readonly revision: number;
  readonly transitioning: boolean;
  readonly components: readonly RuntimeComponentGraphComponentSnapshot[];
  readonly providers: readonly RuntimeComponentGraphProviderSnapshot[];
  readonly blockedCapabilities: readonly string[];
}

export type RuntimeComponentGraphErrorCode =
  | "duplicate_component"
  | "duplicate_provider"
  | "dependency_cycle"
  | "invalid_manifest";

export class RuntimeComponentGraphError extends Error {
  readonly code: RuntimeComponentGraphErrorCode;
  readonly componentIds: readonly string[];
  readonly capabilityId?: string;

  constructor(
    code: RuntimeComponentGraphErrorCode,
    options: { componentIds?: readonly string[]; capabilityId?: string } = {},
  ) {
    super(componentGraphErrorMessage(code, options));
    this.name = "RuntimeComponentGraphError";
    this.code = code;
    this.componentIds = [...(options.componentIds ?? [])];
    this.capabilityId = options.capabilityId;
  }
}

export interface RuntimeComponentGraphOptions {
  readonly eventSink?: RuntimeComponentGraphEventSink;
  readonly componentLifecycleEventSink?: RuntimeComponentLifecycleEventSink;
}

interface PreparedComponent {
  readonly manifest: RuntimeComponentManifest;
  readonly requires: readonly RuntimeCapabilityKey<unknown>[];
  readonly provides: readonly RuntimeCapabilityProvision<unknown>[];
  readonly topologySignature: string;
}

interface PreparedGraph {
  readonly components: Map<string, PreparedComponent>;
  readonly providers: Map<string, PreparedComponent>;
  readonly topologicalOrder: readonly string[];
}

interface ActiveComponent {
  readonly prepared: PreparedComponent;
  readonly scope: RuntimeComponentScope;
  readonly providerComponentIds: Map<string, string>;
  readonly providerInstanceIds: Map<string, string>;
}

interface AvailableProvider {
  readonly componentId: string;
  readonly instanceId: string;
  readonly provision: RuntimeCapabilityProvision<unknown>;
}

interface ComponentStatus {
  readonly state: RuntimeComponentGraphComponentState;
  readonly reason?: RuntimeComponentGraphReason;
  readonly capabilityId?: string;
  readonly providerComponentId?: string;
}

/**
 * Host-owned reactive graph for live runtime components.
 *
 * Reconciliations are serialized. Invalid topology is rejected before the
 * active graph changes, and concurrent requests converge in call order to the
 * latest requested graph.
 */
export class RuntimeComponentGraph {
  private readonly eventSink?: RuntimeComponentGraphEventSink;
  private readonly componentLifecycleEventSink?: RuntimeComponentLifecycleEventSink;
  private active = new Map<string, ActiveComponent>();
  private availableProviders = new Map<string, AvailableProvider>();
  private blockedCapabilities = new Set<string>();
  private desired = new Map<string, PreparedComponent>();
  private statuses = new Map<string, ComponentStatus>();
  private requestedRevision = 0;
  private appliedRevision = 0;
  private transitionQueue: Promise<unknown> = Promise.resolve();
  private transitionCount = 0;

  constructor(options: RuntimeComponentGraphOptions = {}) {
    this.eventSink = options.eventSink;
    this.componentLifecycleEventSink = options.componentLifecycleEventSink;
  }

  reconcile(manifests: readonly RuntimeComponentManifest[]): Promise<RuntimeComponentGraphSnapshot> {
    let prepared: PreparedGraph;
    try {
      prepared = prepareGraph(manifests);
    } catch (error) {
      return Promise.reject(error);
    }
    const revision = ++this.requestedRevision;
    this.transitionCount += 1;
    const apply = async (): Promise<RuntimeComponentGraphSnapshot> => {
      try {
        await this.applyPreparedGraph(prepared, revision);
      } finally {
        this.transitionCount -= 1;
      }
      return this.snapshot();
    };
    const operation = this.transitionQueue.then(apply, apply);
    this.transitionQueue = operation.catch(() => undefined);
    return operation;
  }

  /**
   * Clears a fail-closed provider cleanup block after a trusted supervisor has
   * repaired or verified the external state.
   */
  acknowledgeProviderCleanup<T>(key: RuntimeCapabilityKey<T>): void {
    this.blockedCapabilities.delete(key.id);
  }

  snapshot(): RuntimeComponentGraphSnapshot {
    const components = [...this.desired.values()]
      .map((prepared): RuntimeComponentGraphComponentSnapshot => {
        const active = this.active.get(prepared.manifest.id);
        const status = this.statuses.get(prepared.manifest.id) ?? { state: "waiting" as const };
        return {
          componentId: prepared.manifest.id,
          instanceId: prepared.manifest.instanceId,
          state: active ? "active" : status.state,
          reason: active ? undefined : status.reason,
          capabilityId: active ? undefined : status.capabilityId,
          providerComponentId: active ? undefined : status.providerComponentId,
          providerInstanceIds: active
            ? Object.fromEntries([...active.providerInstanceIds.entries()].sort())
            : {},
          requires: prepared.requires.map((key) => key.id).sort(),
          provides: prepared.provides.map((item) => item.key.id).sort(),
        };
      })
      .sort((left, right) => left.componentId.localeCompare(right.componentId));
    const providers = [...this.availableProviders.entries()]
      .map(([capabilityId, provider]): RuntimeComponentGraphProviderSnapshot => ({
        capabilityId,
        componentId: provider.componentId,
        instanceId: provider.instanceId,
        available: true,
      }))
      .sort((left, right) => left.capabilityId.localeCompare(right.capabilityId));
    return {
      revision: this.appliedRevision,
      transitioning: this.transitionCount > 0,
      components,
      providers,
      blockedCapabilities: [...this.blockedCapabilities].sort(),
    };
  }

  private async applyPreparedGraph(prepared: PreparedGraph, revision: number): Promise<void> {
    const affected = this.affectedActiveComponents(prepared);
    this.markProvidersUnavailable(affected, revision);
    await this.disposeAffected(affected, revision);

    this.desired = prepared.components;
    this.statuses = new Map();
    await this.activateAvailable(prepared, revision);
    this.appliedRevision = revision;
    this.emit({ revision, operation: "graph_reconciled", outcome: "succeeded" });
  }

  private affectedActiveComponents(prepared: PreparedGraph): Set<string> {
    const affected = new Set<string>();
    for (const [componentId, active] of this.active) {
      const next = prepared.components.get(componentId);
      if (
        !next
        || next.manifest.instanceId !== active.prepared.manifest.instanceId
        || next.topologySignature !== active.prepared.topologySignature
      ) {
        affected.add(componentId);
        continue;
      }
      for (const requirement of next.requires) {
        const nextProvider = prepared.providers.get(requirement.id);
        if (
          !nextProvider
          || active.providerInstanceIds.get(requirement.id)
            !== nextProvider.manifest.instanceId
        ) {
          affected.add(componentId);
          break;
        }
      }
    }

    let changed = true;
    while (changed) {
      changed = false;
      for (const [componentId, active] of this.active) {
        if (affected.has(componentId)) continue;
        for (const requirement of active.prepared.requires) {
          const providerId = active.providerComponentIds.get(requirement.id);
          if (providerId && affected.has(providerId)) {
            affected.add(componentId);
            changed = true;
            break;
          }
        }
      }
    }
    return affected;
  }

  private markProvidersUnavailable(affected: Set<string>, revision: number): void {
    for (const [capabilityId, provider] of [...this.availableProviders]) {
      if (!affected.has(provider.componentId)) continue;
      this.availableProviders.delete(capabilityId);
      this.emit({
        revision,
        operation: "provider_unavailable",
        outcome: "started",
        componentId: provider.componentId,
        instanceId: provider.instanceId,
        capabilityId,
      });
    }
  }

  private async disposeAffected(affected: Set<string>, revision: number): Promise<void> {
    const order = activeDisposalOrder(this.active, affected);
    for (const componentId of order) {
      const record = this.active.get(componentId);
      if (!record) continue;
      try {
        await record.scope.dispose();
        this.emit({
          revision,
          operation: "component_deactivated",
          outcome: "succeeded",
          componentId,
          instanceId: record.prepared.manifest.instanceId,
        });
      } catch {
        for (const provision of record.prepared.provides) {
          this.blockedCapabilities.add(provision.key.id);
        }
        this.emit({
          revision,
          operation: "component_failed",
          outcome: "failed",
          componentId,
          instanceId: record.prepared.manifest.instanceId,
          reason: "cleanup_failed",
        });
      } finally {
        this.active.delete(componentId);
      }
    }
  }

  private async activateAvailable(prepared: PreparedGraph, revision: number): Promise<void> {
    for (const componentId of prepared.topologicalOrder) {
      const component = prepared.components.get(componentId)!;
      if (this.active.has(componentId)) {
        this.statuses.set(componentId, { state: "active" });
        continue;
      }
      const unavailable = this.firstUnavailableRequirement(component);
      if (unavailable) {
        this.statuses.set(componentId, { state: "waiting", ...unavailable });
        this.emit({
          revision,
          operation: "component_waiting",
          outcome: "waiting",
          componentId,
          instanceId: component.manifest.instanceId,
          ...unavailable,
        });
        continue;
      }
      const blockedProvision = component.provides.find(
        (provision) => this.blockedCapabilities.has(provision.key.id),
      );
      if (blockedProvision) {
        const status: ComponentStatus = {
          state: "failed",
          reason: "provider_cleanup_failed",
          capabilityId: blockedProvision.key.id,
        };
        this.statuses.set(componentId, status);
        this.emit({
          revision,
          operation: "component_failed",
          outcome: "failed",
          componentId,
          instanceId: component.manifest.instanceId,
          reason: status.reason,
          capabilityId: status.capabilityId,
        });
        continue;
      }

      const providerInstanceIds = new Map<string, string>();
      const providerComponentIds = new Map<string, string>();
      for (const requirement of component.requires) {
        const provider = this.availableProviders.get(requirement.id)!;
        providerInstanceIds.set(requirement.id, provider.instanceId);
        providerComponentIds.set(requirement.id, provider.componentId);
      }
      const scope = new RuntimeComponentScope({
        componentId: component.manifest.id,
        eventSink: this.componentLifecycleEventSink,
      });
      try {
        await scope.activate(() => component.manifest.activate(
          this.activationContext(component, scope, revision),
        ));
        const active: ActiveComponent = {
          prepared: component,
          scope,
          providerComponentIds,
          providerInstanceIds,
        };
        this.active.set(componentId, active);
        for (const provision of component.provides) {
          this.availableProviders.set(provision.key.id, {
            componentId,
            instanceId: component.manifest.instanceId,
            provision,
          });
        }
        this.statuses.set(componentId, { state: "active" });
        this.emit({
          revision,
          operation: "component_activated",
          outcome: "succeeded",
          componentId,
          instanceId: component.manifest.instanceId,
        });
      } catch (error) {
        if (error instanceof AggregateError) {
          for (const provision of component.provides) {
            this.blockedCapabilities.add(provision.key.id);
          }
        }
        this.statuses.set(componentId, { state: "failed", reason: "activation_failed" });
        this.emit({
          revision,
          operation: "component_failed",
          outcome: "failed",
          componentId,
          instanceId: component.manifest.instanceId,
          reason: "activation_failed",
        });
      }
    }
  }

  private firstUnavailableRequirement(
    component: PreparedComponent,
  ): Omit<ComponentStatus, "state"> | undefined {
    for (const requirement of component.requires) {
      const configuredProvider = this.desiredProviderFor(requirement.id);
      const available = this.availableProviders.get(requirement.id);
      if (!configuredProvider) {
        return { reason: "missing_requirement", capabilityId: requirement.id };
      }
      if (!available) {
        return {
          reason: "provider_inactive",
          capabilityId: requirement.id,
          providerComponentId: configuredProvider.manifest.id,
        };
      }
    }
    return undefined;
  }

  private desiredProviderFor(capabilityId: string): PreparedComponent | undefined {
    for (const component of this.desired.values()) {
      if (component.provides.some((provision) => provision.key.id === capabilityId)) {
        return component;
      }
    }
    return undefined;
  }

  private activationContext(
    component: PreparedComponent,
    scope: RuntimeComponentScope,
    revision: number,
  ): RuntimeComponentActivationContext {
    const declaredRequirements = new Set(component.requires.map((key) => key.id));
    const assertDeclared = (key: RuntimeCapabilityKey<unknown>): void => {
      if (!declaredRequirements.has(key.id)) {
        throw new Error(
          `runtime component ${component.manifest.id} did not declare requirement ${key.id}`,
        );
      }
    };
    return {
      componentId: component.manifest.id,
      instanceId: component.manifest.instanceId,
      revision,
      scope,
      get: <T>(key: RuntimeCapabilityKey<T>): T => {
        assertDeclared(key);
        const provider = this.availableProviders.get(key.id);
        if (!provider) throw new Error(`runtime capability ${key.id} is unavailable`);
        return provider.provision.value as T;
      },
      providerIdentity: <T>(key: RuntimeCapabilityKey<T>): string => {
        assertDeclared(key);
        const provider = this.availableProviders.get(key.id);
        if (!provider) throw new Error(`runtime capability ${key.id} is unavailable`);
        return provider.instanceId;
      },
    };
  }

  private emit(event: RuntimeComponentGraphEvent): void {
    try {
      this.eventSink?.onRuntimeComponentGraphEvent(event);
    } catch {
      // Graph observers are not component-owned failure points.
    }
  }
}

export function defineRuntimeCapability<T>(id: string): RuntimeCapabilityKey<T> {
  return Object.freeze({ id: validateGraphIdentifier(id, "capability") });
}

export function provideRuntimeCapability<T>(
  key: RuntimeCapabilityKey<T>,
  value: T,
): RuntimeCapabilityProvision<T> {
  return Object.freeze({ key, value });
}

/** Validate a desired graph without activating or disposing any component. */
export function validateRuntimeComponentGraph(
  manifests: readonly RuntimeComponentManifest[],
): void {
  prepareGraph(manifests);
}

function prepareGraph(manifests: readonly RuntimeComponentManifest[]): PreparedGraph {
  const components = new Map<string, PreparedComponent>();
  const providers = new Map<string, PreparedComponent>();
  for (const raw of manifests) {
    if (!raw || typeof raw !== "object" || typeof raw.activate !== "function") {
      throw new RuntimeComponentGraphError("invalid_manifest");
    }
    const id = validateGraphIdentifier(raw.id, "component");
    const instanceId = validateGraphIdentifier(raw.instanceId, "component instance");
    if (components.has(id)) {
      throw new RuntimeComponentGraphError("duplicate_component", { componentIds: [id] });
    }
    const requires = readRequirements(raw.requires, id);
    const provides = readProvisions(raw.provides, id);
    validateUniqueCapabilities(requires.map((key) => key.id), id);
    validateUniqueCapabilities(provides.map((item) => item.key.id), id);
    for (const key of requires) validateGraphIdentifier(key.id, "capability");
    for (const provision of provides) validateGraphIdentifier(provision.key.id, "capability");
    const manifest: RuntimeComponentManifest = { ...raw, id, instanceId, requires, provides };
    const prepared: PreparedComponent = {
      manifest,
      requires,
      provides,
      topologySignature: JSON.stringify({
        requires: requires.map((key) => key.id).sort(),
        provides: provides.map((item) => item.key.id).sort(),
      }),
    };
    components.set(id, prepared);
    for (const provision of provides) {
      const existing = providers.get(provision.key.id);
      if (existing) {
        throw new RuntimeComponentGraphError("duplicate_provider", {
          capabilityId: provision.key.id,
          componentIds: [existing.manifest.id, id].sort(),
        });
      }
      providers.set(provision.key.id, prepared);
    }
  }
  return {
    components,
    providers,
    topologicalOrder: topologicalOrder(components, providers),
  };
}

function topologicalOrder(
  components: Map<string, PreparedComponent>,
  providers: Map<string, PreparedComponent>,
): string[] {
  const dependencies = new Map<string, Set<string>>();
  const dependents = new Map<string, Set<string>>();
  for (const component of components.values()) {
    const deps = new Set<string>();
    for (const requirement of component.requires) {
      const provider = providers.get(requirement.id);
      if (!provider) continue;
      deps.add(provider.manifest.id);
      const providerDependents = dependents.get(provider.manifest.id) ?? new Set<string>();
      providerDependents.add(component.manifest.id);
      dependents.set(provider.manifest.id, providerDependents);
    }
    dependencies.set(component.manifest.id, deps);
  }
  const ready = [...components.keys()]
    .filter((id) => dependencies.get(id)?.size === 0)
    .sort();
  const order: string[] = [];
  while (ready.length > 0) {
    const id = ready.shift()!;
    order.push(id);
    for (const dependentId of dependents.get(id) ?? []) {
      const remaining = dependencies.get(dependentId)!;
      remaining.delete(id);
      if (remaining.size === 0) {
        ready.push(dependentId);
        ready.sort();
      }
    }
  }
  if (order.length !== components.size) {
    const cycle = [...components.keys()].filter((id) => !order.includes(id)).sort();
    throw new RuntimeComponentGraphError("dependency_cycle", { componentIds: cycle });
  }
  return order;
}

function activeDisposalOrder(
  active: Map<string, ActiveComponent>,
  affected: Set<string>,
): string[] {
  const depths = new Map<string, number>();
  const depth = (componentId: string, visiting = new Set<string>()): number => {
    const known = depths.get(componentId);
    if (known !== undefined) return known;
    if (visiting.has(componentId)) return 0;
    visiting.add(componentId);
    const record = active.get(componentId);
    let result = 0;
    for (const requirement of record?.prepared.requires ?? []) {
      const providerId = record?.providerComponentIds.get(requirement.id);
      if (providerId && affected.has(providerId)) {
        result = Math.max(result, depth(providerId, visiting) + 1);
      }
    }
    visiting.delete(componentId);
    depths.set(componentId, result);
    return result;
  };
  return [...affected].sort((left, right) =>
    depth(right) - depth(left) || left.localeCompare(right),
  );
}

function validateUniqueCapabilities(ids: readonly string[], componentId: string): void {
  const seen = new Set<string>();
  for (const id of ids) {
    if (seen.has(id)) {
      throw new RuntimeComponentGraphError("invalid_manifest", {
        componentIds: [componentId],
        capabilityId: id,
      });
    }
    seen.add(id);
  }
}

function readRequirements(
  value: readonly RuntimeCapabilityKey<unknown>[] | undefined,
  componentId: string,
): RuntimeCapabilityKey<unknown>[] {
  if (value !== undefined && !Array.isArray(value)) {
    throw new RuntimeComponentGraphError("invalid_manifest", { componentIds: [componentId] });
  }
  const requirements = [...(value ?? [])];
  for (const requirement of requirements) {
    if (!requirement || typeof requirement !== "object") {
      throw new RuntimeComponentGraphError("invalid_manifest", { componentIds: [componentId] });
    }
    validateGraphIdentifier(requirement.id, "capability");
  }
  return requirements;
}

function readProvisions(
  value: readonly RuntimeCapabilityProvision<unknown>[] | undefined,
  componentId: string,
): RuntimeCapabilityProvision<unknown>[] {
  if (value !== undefined && !Array.isArray(value)) {
    throw new RuntimeComponentGraphError("invalid_manifest", { componentIds: [componentId] });
  }
  const provisions = [...(value ?? [])];
  for (const provision of provisions) {
    if (!provision || typeof provision !== "object" || !provision.key) {
      throw new RuntimeComponentGraphError("invalid_manifest", { componentIds: [componentId] });
    }
    validateGraphIdentifier(provision.key.id, "capability");
  }
  return provisions;
}

function validateGraphIdentifier(value: string, kind: string): string {
  if (typeof value !== "string") throw new RuntimeComponentGraphError("invalid_manifest");
  const normalized = value.trim();
  if (
    !normalized
    || normalized.length > 160
    || /[\u0000-\u001f\u007f]/.test(normalized)
  ) {
    throw new RuntimeComponentGraphError("invalid_manifest", {
      componentIds: kind.startsWith("component") ? [normalized] : [],
      capabilityId: kind === "capability" ? normalized : undefined,
    });
  }
  return normalized;
}

function componentGraphErrorMessage(
  code: RuntimeComponentGraphErrorCode,
  options: { componentIds?: readonly string[]; capabilityId?: string },
): string {
  switch (code) {
    case "duplicate_component":
      return `duplicate runtime component: ${options.componentIds?.join(", ") ?? "unknown"}`;
    case "duplicate_provider":
      return `duplicate provider for runtime capability ${options.capabilityId ?? "unknown"}`;
    case "dependency_cycle":
      return `runtime component dependency cycle: ${options.componentIds?.join(" -> ") ?? "unknown"}`;
    case "invalid_manifest":
      return "invalid runtime component manifest";
  }
}
