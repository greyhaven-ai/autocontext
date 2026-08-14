import type { ClientMessage, ServerMessage } from "./protocol.js";
import {
  validateImageAttachmentsForInference,
  type ValidatedImageAttachment,
} from "../types/index.js";

export interface ChatAgentCommandRunManager {
  getState(): { runId: string | null };
  chatAgent(
    role: string,
    message: string,
    imageAttachments?: readonly ValidatedImageAttachment[],
    expectedRunId?: string | null,
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
  const expectedRunId = opts.runManager.getState().runId;
  const imageAttachments = await validateImageAttachmentsForInference(
    opts.command.image_attachments ?? [],
  );
  const text = await opts.runManager.chatAgent(
    opts.command.role,
    opts.command.message,
    imageAttachments,
    expectedRunId,
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
