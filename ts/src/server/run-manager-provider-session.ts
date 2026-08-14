import { loadSettings, type AppSettings } from "../config/index.js";
import {
  buildRoleProviderBundle,
  type GenerationRole,
  type ProviderCompositionOpts,
  type RoleProviderBundle,
} from "../providers/index.js";
import { providerSupportsImageAttachments } from "../providers/image-capability.js";
import {
  EXPLICIT_PROVIDER_CLASS,
  PROVIDER_HOSTING,
  ROUTED_GENERATION_ROLES,
} from "../providers/role-routing.js";

export interface ProviderSessionOverride {
  providerType: string;
  apiKey?: string;
  baseUrl?: string;
  model?: string;
}

export interface RunManagerProviderSessionDeps {
  loadSettings?: () => AppSettings;
  buildRoleProviderBundle?: (
    settings: AppSettings,
    overrides?: Partial<ProviderSessionOverride>,
    opts?: ProviderCompositionOpts,
  ) => RoleProviderBundle;
}

export class RunManagerProviderSession {
  readonly #defaults: ProviderSessionOverride;
  readonly #deps: RunManagerProviderSessionDeps;
  #providerOverride: ProviderSessionOverride | null | undefined;

  constructor(defaults: Partial<ProviderSessionOverride>, deps?: RunManagerProviderSessionDeps) {
    this.#defaults = {
      providerType: defaults.providerType ?? "",
      ...(defaults.apiKey ? { apiKey: defaults.apiKey } : {}),
      ...(defaults.baseUrl ? { baseUrl: defaults.baseUrl } : {}),
      ...(defaults.model ? { model: defaults.model } : {}),
    };
    this.#deps = deps ?? {};
  }

  getActiveProviderType(): string | null {
    if (this.#providerOverride === null) {
      return null;
    }
    return (
      this.#providerOverride?.providerType ??
      this.#defaults.providerType ??
      this.#loadSettings().agentProvider
    );
  }

  setActiveProvider(config: ProviderSessionOverride): void {
    this.#providerOverride = {
      providerType: config.providerType.trim().toLowerCase(),
      ...(config.apiKey ? { apiKey: config.apiKey } : {}),
      ...(config.baseUrl ? { baseUrl: config.baseUrl } : {}),
      ...(config.model ? { model: config.model } : {}),
    };
  }

  clearActiveProvider(): void {
    this.#providerOverride = null;
  }

  resolveProviderBundle(
    settings = this.#loadSettings(),
    opts?: ProviderCompositionOpts,
  ): RoleProviderBundle {
    if (this.#providerOverride === null) {
      throw new Error("No active provider configured for this session. Use /login or /provider.");
    }

    const overrides = this.#providerOverride ?? this.#defaults;
    // A defined #providerOverride means setActiveProvider() ran (e.g. switch_provider or
    // login) — a deliberate mid-session decision that should win over a pinned env var.
    // Falling back to #defaults (the constructor's startup providerType) keeps the
    // construction-time precedent where a live env var still wins.
    const isSessionExplicit = this.#providerOverride !== undefined;
    const resolvedOpts: ProviderCompositionOpts | undefined = isSessionExplicit
      ? { ...opts, preferProviderOverride: true }
      : opts;
    return this.#buildRoleProviderBundle(
      settings,
      {
        providerType: overrides.providerType,
        apiKey: overrides.apiKey,
        baseUrl: overrides.baseUrl,
        model: overrides.model,
      },
      resolvedOpts,
    );
  }

  buildProvider(role?: GenerationRole, settings = this.#loadSettings()) {
    const bundle = this.resolveProviderBundle(settings);
    if (role) {
      return bundle.roleProviders[role] ?? bundle.defaultProvider;
    }
    return bundle.defaultProvider;
  }

  supportsImageAttachments(role?: GenerationRole, settings = this.#loadSettings()): boolean {
    const bundle = this.resolveProviderBundle(settings);
    try {
      const provider = role
        ? bundle.roleProviders[role] ?? bundle.defaultProvider
        : bundle.defaultProvider;
      const model = role ? bundle.roleModels[role] : bundle.defaultConfig.model;
      return providerSupportsImageAttachments(provider, model);
    } finally {
      bundle.close?.();
    }
  }

  supportsInteractiveImageAttachments(settings = this.#loadSettings()): boolean {
    const bundle = this.resolveProviderBundle(settings);
    try {
      const roles: GenerationRole[] = ["competitor", "analyst", "coach", "architect", "curator"];
      return roles.every((role) => {
        const provider = bundle.roleProviders[role] ?? bundle.defaultProvider;
        return providerSupportsImageAttachments(provider, bundle.roleModels[role]);
      });
    } finally {
      bundle.close?.();
    }
  }

  describeRoutingContext(settings = this.#loadSettings()): {
    provider: string;
    model?: string;
    hostingClass?: string;
    capabilityTier?: string;
    roles: Record<string, { provider: string; model: string; capabilityTier?: string }>;
  } {
    const fallbackProvider = this.getActiveProviderType() ?? "none";
    try {
      const bundle = this.resolveProviderBundle(settings);
      try {
        const provider = bundle.defaultConfig.providerType;
        const roles = Object.fromEntries(ROUTED_GENERATION_ROLES.map((role) => {
          const route = bundle.roleRoutes?.[role];
          return [role, {
            provider: route?.providerType ?? provider,
            model: bundle.roleModels[role] ?? route?.model ?? bundle.defaultProvider.defaultModel(),
            ...(route?.providerClass ? { capabilityTier: route.providerClass } : {}),
          }];
        }));
        return {
          provider,
          model: bundle.defaultConfig.model ?? bundle.defaultProvider.defaultModel(),
          hostingClass: PROVIDER_HOSTING[provider] === "local" ? "local" : "remote",
          capabilityTier: EXPLICIT_PROVIDER_CLASS[provider] ?? "unknown",
          roles,
        };
      } finally {
        bundle.close?.();
      }
    } catch {
      return { provider: fallbackProvider, roles: {} };
    }
  }

  #loadSettings(): AppSettings {
    return (this.#deps.loadSettings ?? loadSettings)();
  }

  #buildRoleProviderBundle(
    settings: AppSettings,
    overrides?: Partial<ProviderSessionOverride>,
    opts?: ProviderCompositionOpts,
  ): RoleProviderBundle {
    if (opts) {
      return (this.#deps.buildRoleProviderBundle ?? buildRoleProviderBundle)(
        settings,
        overrides,
        opts,
      );
    }
    return (this.#deps.buildRoleProviderBundle ?? buildRoleProviderBundle)(settings, overrides);
  }
}
