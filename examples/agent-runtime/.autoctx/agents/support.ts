import type { AutoctxAgentContext } from "autoctx/agent-runtime";

type SupportPayload = {
  threadId?: string;
  message: string;
};

export const triggers = { webhook: true };

export default async function supportAgent(
  { init, payload }: AutoctxAgentContext<SupportPayload>,
) {
  const runtime = await init();
  const session = await runtime.session(payload.threadId ?? "default");
  return session.prompt(payload.message, { role: "support-triager" });
}
