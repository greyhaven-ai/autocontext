import type { ClientMessage, ServerMessage } from "./protocol.js";
import {
  validateImageAttachments,
  type ValidatedImageAttachment,
} from "../types/index.js";

export interface ChatAgentCommandRunManager {
  chatAgent(
    role: string,
    message: string,
    imageAttachments?: readonly ValidatedImageAttachment[],
  ): Promise<string>;
}

export function buildChatResponseMessage(opts: {
  clientRunId?: string;
  commandId?: string;
  role: string;
  text: string;
}): ServerMessage {
  return {
    type: "chat_response",
    role: opts.role,
    text: opts.text,
    ...(opts.clientRunId ? { client_run_id: opts.clientRunId } : {}),
    ...(opts.commandId ? { command_id: opts.commandId } : {}),
  };
}

export async function executeChatAgentCommand(opts: {
  command: Extract<ClientMessage, { type: "chat_agent" }>;
  runManager: ChatAgentCommandRunManager;
}): Promise<ServerMessage[]> {
  const imageAttachments = validateImageAttachments(opts.command.image_attachments ?? []);
  const text = await opts.runManager.chatAgent(
    opts.command.role,
    opts.command.message,
    imageAttachments,
  );
  return [
    buildChatResponseMessage({
      clientRunId: opts.command.client_run_id,
      commandId: opts.command.command_id,
      role: opts.command.role,
      text,
    }),
  ];
}
