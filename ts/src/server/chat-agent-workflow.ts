import type { GenerationRole, RoleProviderBundle } from "../providers/index.js";
import { assertProviderSupportsImageAttachments } from "../providers/index.js";
import type { ValidatedImageAttachment } from "../types/index.js";
import type { RunManagerState } from "./run-manager.js";

export function normalizeChatAgentRole(role: string): GenerationRole | undefined {
  return role === "competitor"
    || role === "analyst"
    || role === "coach"
    || role === "architect"
    || role === "curator"
    ? role
    : undefined;
}

export function buildChatAgentUserPrompt(opts: {
  role: string;
  message: string;
  state: RunManagerState;
}): string {
  return [
    `[${opts.role}]`,
    "You are helping from the interactive autocontext control plane.",
    `Run active: ${opts.state.active ? "yes" : "no"}`,
    `Scenario: ${opts.state.scenario ?? "none"}`,
    `Generation: ${opts.state.generation ?? 0}`,
    `Phase: ${opts.state.phase ?? "idle"}`,
    `Operator message: ${opts.message}`,
  ].join("\n");
}

export async function executeChatAgentInteraction(opts: {
  role: string;
  message: string;
  state: RunManagerState;
  resolveProviderBundle: () => RoleProviderBundle;
  imageAttachments?: readonly ValidatedImageAttachment[];
}): Promise<string> {
  const normalizedRole = normalizeChatAgentRole(opts.role);
  const bundle = opts.resolveProviderBundle();
  const provider = normalizedRole
    ? bundle.roleProviders[normalizedRole] ?? bundle.defaultProvider
    : bundle.defaultProvider;
  try {
    const model = normalizedRole ? bundle.roleModels[normalizedRole] : bundle.defaultConfig.model;
    assertProviderSupportsImageAttachments(provider, model, opts.imageAttachments);
    const response = await provider.complete({
      systemPrompt: "",
      model,
      userPrompt: buildChatAgentUserPrompt({
        role: opts.role,
        message: opts.message,
        state: opts.state,
      }),
      imageAttachments: opts.imageAttachments,
    });
    return response.text;
  } finally {
    bundle.close?.();
  }
}
