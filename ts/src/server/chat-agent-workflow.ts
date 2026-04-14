import type { GenerationRole, RoleProviderBundle } from "../providers/index.js";
import type { LLMProvider } from "../types/index.js";
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
  buildProvider: (role?: GenerationRole) => LLMProvider;
}): Promise<string> {
  const normalizedRole = normalizeChatAgentRole(opts.role);
  const bundle = opts.resolveProviderBundle();
  const provider = opts.buildProvider(normalizedRole);
  const response = await provider.complete({
    systemPrompt: "",
    model: normalizedRole ? bundle.roleModels[normalizedRole] : bundle.defaultConfig.model,
    userPrompt: buildChatAgentUserPrompt({
      role: opts.role,
      message: opts.message,
      state: opts.state,
    }),
  });
  return response.text;
}
