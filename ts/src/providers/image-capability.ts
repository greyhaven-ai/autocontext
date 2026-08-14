import {
  ProviderError,
  type LLMProvider,
  type ValidatedImageAttachment,
} from "../types/index.js";

export function providerSupportsImageAttachments(
  provider: LLMProvider,
  model?: string,
): boolean {
  try {
    return provider.supportsImageAttachments?.(model ?? provider.defaultModel()) === true;
  } catch {
    return false;
  }
}

export function assertProviderSupportsImageAttachments(
  provider: LLMProvider,
  model: string | undefined,
  attachments: readonly ValidatedImageAttachment[] | undefined,
): void {
  if (!attachments?.length) return;
  const resolvedModel = model ?? provider.defaultModel();
  if (!providerSupportsImageAttachments(provider, resolvedModel)) {
    throw new ProviderError(
      `Provider '${provider.name}' model '${resolvedModel}' does not support image attachments`,
    );
  }
}
