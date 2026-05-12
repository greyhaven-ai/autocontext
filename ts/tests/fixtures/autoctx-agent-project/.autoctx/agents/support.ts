import type { AutoctxAgentContext } from "../../../../../src/agent-runtime/index.js";

export const triggers = { webhook: true };

export default async function supportAgent(
  { init, payload }: AutoctxAgentContext<{ threadId?: string; message: string }>,
) {
  const runtime = await init({ model: "test/fake-runtime" });
  const session = await runtime.session(payload.threadId ?? "default");
  return session.prompt(payload.message, { role: "support-triager" });
}
