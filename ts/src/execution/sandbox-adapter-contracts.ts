export const SANDBOX_CAPABILITY_NAMES = [
  "snapshot",
  "restore",
  "prebuild_repo_image",
  "warm",
  "resolve_tunnel_ports",
] as const;

export type SandboxCapabilityName = (typeof SANDBOX_CAPABILITY_NAMES)[number];
export type SandboxRequestedBootMode = "fresh" | "restore" | "repo_image" | "build" | "warm";
export type SandboxBootMode = "fresh" | "restored" | "repo_image" | "build" | "warmed";
export type UnsupportedSandboxCapabilityPolicy = "fail_closed" | "degrade_to_fresh";
export type SandboxCapabilityRecord = Record<SandboxCapabilityName, boolean>;

export interface SandboxCapabilityRequest {
  readonly [key: string]: unknown;
}

export interface SandboxCapabilityResult {
  readonly [key: string]: unknown;
}

export interface SandboxSnapshotAdapter {
  snapshot(
    request: SandboxCapabilityRequest,
  ): SandboxCapabilityResult | Promise<SandboxCapabilityResult>;
}

export interface SandboxRestoreAdapter {
  restore(
    request: SandboxCapabilityRequest,
  ): SandboxCapabilityResult | Promise<SandboxCapabilityResult>;
}

export interface SandboxRepoImageAdapter {
  prebuildRepoImage(
    request: SandboxCapabilityRequest,
  ): SandboxCapabilityResult | Promise<SandboxCapabilityResult>;
}

export interface SandboxWarmAdapter {
  warm(
    request: SandboxCapabilityRequest,
  ): SandboxCapabilityResult | Promise<SandboxCapabilityResult>;
}

export interface SandboxTunnelPortAdapter {
  resolveTunnelPorts(
    request: SandboxCapabilityRequest,
  ): SandboxCapabilityResult | Promise<SandboxCapabilityResult>;
}

export interface SandboxStartupPlan {
  readonly session_id: string;
  readonly requested_boot_mode: SandboxRequestedBootMode;
  readonly boot_mode: SandboxBootMode;
  readonly capability: SandboxCapabilityName | "";
  readonly supported: boolean;
  readonly degraded: boolean;
  readonly terminal: boolean;
  readonly unsupported_policy: UnsupportedSandboxCapabilityPolicy;
  readonly reason: string;
  readonly lifecycle_hooks: readonly ("setup" | "start")[];
}

export interface PlanSandboxStartupOptions {
  readonly sessionId: string;
  readonly requestedBootMode?: SandboxRequestedBootMode;
  readonly capabilities?: unknown;
  readonly unsupportedPolicy?: UnsupportedSandboxCapabilityPolicy;
  readonly snapshotId?: string;
  readonly repoImageId?: string;
  readonly provider?: string;
}

const BOOT_MODE_BY_REQUEST: Record<SandboxRequestedBootMode, SandboxBootMode> = {
  fresh: "fresh",
  restore: "restored",
  repo_image: "repo_image",
  build: "build",
  warm: "warmed",
};

const REQUIRED_CAPABILITY_BY_REQUEST: Record<
  SandboxRequestedBootMode,
  SandboxCapabilityName | null
> = {
  fresh: null,
  restore: "restore",
  repo_image: "prebuild_repo_image",
  build: null,
  warm: "warm",
};

export function normalizeSandboxAdapterCapabilities(source: unknown): SandboxCapabilityRecord {
  const raw = capabilityRecord(source);
  return Object.fromEntries(
    SANDBOX_CAPABILITY_NAMES.map((name) => [name, raw[name] === true]),
  ) as SandboxCapabilityRecord;
}

export function planSandboxStartup(options: PlanSandboxStartupOptions): SandboxStartupPlan {
  const requestedBootMode = options.requestedBootMode ?? "fresh";
  const unsupportedPolicy = options.unsupportedPolicy ?? "degrade_to_fresh";
  assertRequestedBootMode(requestedBootMode);
  assertUnsupportedPolicy(unsupportedPolicy);
  void options.repoImageId;
  void options.provider;

  const capability = REQUIRED_CAPABILITY_BY_REQUEST[requestedBootMode];
  const bootMode = BOOT_MODE_BY_REQUEST[requestedBootMode];
  if (!capability) {
    return startupPlan({
      sessionId: options.sessionId,
      requestedBootMode,
      bootMode,
      capability: "",
      supported: true,
      degraded: false,
      terminal: false,
      unsupportedPolicy,
      reason: "",
      lifecycleHooks: lifecycleHooksForBootMode(bootMode),
    });
  }

  const supported = normalizeSandboxAdapterCapabilities(options.capabilities)[capability];
  if (supported) {
    const missingRefReason = missingRequiredRefReason(requestedBootMode, {
      snapshotId: options.snapshotId,
    });
    if (missingRefReason) {
      return policyGuardedPlan({
        sessionId: options.sessionId,
        requestedBootMode,
        bootMode,
        capability,
        supported: true,
        unsupportedPolicy,
        reason: missingRefReason,
      });
    }
    return startupPlan({
      sessionId: options.sessionId,
      requestedBootMode,
      bootMode,
      capability,
      supported: true,
      degraded: false,
      terminal: false,
      unsupportedPolicy,
      reason: "",
      lifecycleHooks: lifecycleHooksForBootMode(bootMode),
    });
  }

  return policyGuardedPlan({
    sessionId: options.sessionId,
    requestedBootMode,
    bootMode,
    capability,
    supported: false,
    unsupportedPolicy,
    reason: `unsupported_${capability}`,
  });
}

export function lifecycleHooksForBootMode(
  bootMode: SandboxBootMode,
): readonly ("setup" | "start")[] {
  assertBootMode(bootMode);
  return bootMode === "restored" || bootMode === "repo_image" || bootMode === "warmed"
    ? ["start"]
    : ["setup", "start"];
}

function policyGuardedPlan(input: {
  readonly sessionId: string;
  readonly requestedBootMode: SandboxRequestedBootMode;
  readonly bootMode: SandboxBootMode;
  readonly capability: SandboxCapabilityName;
  readonly supported: boolean;
  readonly unsupportedPolicy: UnsupportedSandboxCapabilityPolicy;
  readonly reason: string;
}): SandboxStartupPlan {
  if (input.unsupportedPolicy === "fail_closed") {
    return startupPlan({
      sessionId: input.sessionId,
      requestedBootMode: input.requestedBootMode,
      bootMode: input.bootMode,
      capability: input.capability,
      supported: input.supported,
      degraded: false,
      terminal: true,
      unsupportedPolicy: input.unsupportedPolicy,
      reason: input.reason,
      lifecycleHooks: [],
    });
  }
  return startupPlan({
    sessionId: input.sessionId,
    requestedBootMode: input.requestedBootMode,
    bootMode: "fresh",
    capability: input.capability,
    supported: input.supported,
    degraded: true,
    terminal: false,
    unsupportedPolicy: input.unsupportedPolicy,
    reason: input.reason,
    lifecycleHooks: lifecycleHooksForBootMode("fresh"),
  });
}

function startupPlan(input: {
  readonly sessionId: string;
  readonly requestedBootMode: SandboxRequestedBootMode;
  readonly bootMode: SandboxBootMode;
  readonly capability: SandboxCapabilityName | "";
  readonly supported: boolean;
  readonly degraded: boolean;
  readonly terminal: boolean;
  readonly unsupportedPolicy: UnsupportedSandboxCapabilityPolicy;
  readonly reason: string;
  readonly lifecycleHooks: readonly ("setup" | "start")[];
}): SandboxStartupPlan {
  return {
    session_id: input.sessionId,
    requested_boot_mode: input.requestedBootMode,
    boot_mode: input.bootMode,
    capability: input.capability,
    supported: input.supported,
    degraded: input.degraded,
    terminal: input.terminal,
    unsupported_policy: input.unsupportedPolicy,
    reason: input.reason,
    lifecycle_hooks: input.lifecycleHooks,
  };
}

function missingRequiredRefReason(
  requestedBootMode: SandboxRequestedBootMode,
  refs: { readonly snapshotId?: string },
): string {
  if (requestedBootMode === "restore" && !hasRef(refs.snapshotId)) {
    return "missing_snapshot_ref";
  }
  return "";
}

function hasRef(value: string | undefined): boolean {
  return typeof value === "string" && value.trim().length > 0;
}

function capabilityRecord(source: unknown): Record<string, unknown> {
  if (!source || typeof source !== "object") {
    return {};
  }
  if (isRecord(source)) {
    if (isRecord(source.sandboxCapabilities)) {
      return source.sandboxCapabilities;
    }
    if (isRecord(source.sandbox_capabilities)) {
      return source.sandbox_capabilities;
    }
    return source;
  }
  return {};
}

function isRecord(value: unknown): value is Record<string, unknown> {
  return typeof value === "object" && value !== null && !Array.isArray(value);
}

function assertRequestedBootMode(value: string): asserts value is SandboxRequestedBootMode {
  if (!(value in BOOT_MODE_BY_REQUEST)) {
    throw new RangeError(`Unsupported sandbox boot request: ${value}`);
  }
}

function assertBootMode(value: string): asserts value is SandboxBootMode {
  if (!Object.values(BOOT_MODE_BY_REQUEST).includes(value as SandboxBootMode)) {
    throw new RangeError(`Unsupported sandbox boot mode: ${value}`);
  }
}

function assertUnsupportedPolicy(
  value: string,
): asserts value is UnsupportedSandboxCapabilityPolicy {
  if (value !== "fail_closed" && value !== "degrade_to_fresh") {
    throw new RangeError(`Unsupported sandbox capability policy: ${value}`);
  }
}
