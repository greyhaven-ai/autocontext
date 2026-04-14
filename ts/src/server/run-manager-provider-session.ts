import { loadSettings, type AppSettings } from "../config/index.js";
import {
  buildRoleProviderBundle,
  type GenerationRole,
  type RoleProviderBundle,
} from "../providers/index.js";

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
    return this.#providerOverride?.providerType
      ?? this.#defaults.providerType
      ?? this.#loadSettings().agentProvider;
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

  resolveProviderBundle(settings = this.#loadSettings()): RoleProviderBundle {
    if (this.#providerOverride === null) {
      throw new Error("No active provider configured for this session. Use /login or /provider.");
    }

    const overrides = this.#providerOverride ?? this.#defaults;
    return this.#buildRoleProviderBundle(settings, {
      providerType: overrides.providerType,
      apiKey: overrides.apiKey,
      baseUrl: overrides.baseUrl,
      model: overrides.model,
    });
  }

  buildProvider(role?: GenerationRole, settings = this.#loadSettings()) {
    const bundle = this.resolveProviderBundle(settings);
    if (role) {
      return bundle.roleProviders[role] ?? bundle.defaultProvider;
    }
    return bundle.defaultProvider;
  }

  #loadSettings(): AppSettings {
    return (this.#deps.loadSettings ?? loadSettings)();
  }

  #buildRoleProviderBundle(
    settings: AppSettings,
    overrides?: Partial<ProviderSessionOverride>,
  ): RoleProviderBundle {
    return (this.#deps.buildRoleProviderBundle ?? buildRoleProviderBundle)(settings, overrides);
  }
}
