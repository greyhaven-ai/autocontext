import {
  ProviderError,
  type LLMProvider,
  type ProviderIsolationPolicy,
} from "../types/index.js";

export interface AcquiredProviderIsolation {
  provider: LLMProvider;
  owned: boolean;
}

export const NO_TOOLS_PROVIDER_ISOLATION: Readonly<ProviderIsolationPolicy> = Object.freeze({
  noTools: true,
});

export function acquireProviderIsolation(
  provider: LLMProvider,
  policy: ProviderIsolationPolicy,
): AcquiredProviderIsolation {
  if (!provider.createIsolatedProvider) {
    if (policy.noTools && provider.isStatelessNoToolsProvider !== true) {
      throw new ProviderError(
        `Provider ${JSON.stringify(provider.name)} cannot guarantee no-tools isolation`,
      );
    }
    return { provider, owned: false };
  }

  const isolated = provider.createIsolatedProvider(policy);
  if (!isolated) {
    throw new ProviderError(
      `Provider ${JSON.stringify(provider.name)} returned no isolated provider`,
    );
  }
  if (isolated === provider) {
    if (policy.noTools && provider.isStatelessNoToolsProvider !== true) {
      throw new ProviderError(
        `Provider ${JSON.stringify(provider.name)} reused shared state for no-tools isolation`,
      );
    }
    return { provider, owned: false };
  }
  return { provider: isolated, owned: true };
}

export function closeProviderIsolation(acquired: AcquiredProviderIsolation): void {
  if (acquired.owned) acquired.provider.close?.();
}
