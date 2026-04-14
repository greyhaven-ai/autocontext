import type { ClientMessage, ServerMessage } from "./protocol.js";

export interface ChatAgentCommandRunManager {
  chatAgent(role: string, message: string): Promise<string>;
}

export function buildChatResponseMessage(opts: {
  role: string;
  text: string;
}): ServerMessage {
  return {
    type: "chat_response",
    role: opts.role,
    text: opts.text,
  };
}

export async function executeChatAgentCommand(opts: {
  command: Extract<ClientMessage, { type: "chat_agent" }>;
  runManager: ChatAgentCommandRunManager;
}): Promise<ServerMessage[]> {
  const text = await opts.runManager.chatAgent(opts.command.role, opts.command.message);
  return [
    buildChatResponseMessage({
      role: opts.command.role,
      text,
    }),
  ];
}
