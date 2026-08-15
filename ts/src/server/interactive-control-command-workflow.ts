import type { ClientMessage, ServerMessage } from "./protocol.js";
import { buildEnvironmentMessage } from "./websocket-session-bootstrap.js";
import {
  validateImageAttachmentsForInference,
  type ValidatedImageAttachment,
} from "../types/index.js";

export interface InteractiveControlRunManager {
  pause(expectedRunId?: string | null): void;
  resume(expectedRunId?: string | null): void;
  injectHint(
    text: string,
    imageAttachments?: readonly ValidatedImageAttachment[],
    expectedRunId?: string | null,
  ): void;
  getState?(): { runId: string | null };
  overrideGate(
    decision: "advance" | "retry" | "rollback",
    expectedRunId?: string | null,
  ): void;
  startRun(
    scenario: string,
    generations: number,
    opts?: { requirePlaybookApproval?: boolean },
  ): Promise<string>;
  getEnvironmentInfo(): {
    scenarios: Array<{ name: string; description: string }>;
    executors: Array<{ mode: string; available: boolean; description: string }>;
    currentExecutor: string;
    agentProvider: string;
  };
}

export function buildRunAcceptedMessage(opts: {
  clientRunId?: string;
  commandId?: string;
  runId: string;
  scenario: string;
  generations: number;
}): ServerMessage {
  return {
    type: "run_accepted",
    run_id: opts.runId,
    scenario: opts.scenario,
    generations: opts.generations,
    ...(opts.clientRunId ? { client_run_id: opts.clientRunId } : {}),
    ...(opts.commandId ? { command_id: opts.commandId } : {}),
  };
}

export async function executeInteractiveControlCommand(opts: {
  command: Extract<
    ClientMessage,
    { type: "pause" | "resume" | "inject_hint" | "override_gate" | "start_run" | "list_scenarios" }
  >;
  runManager: InteractiveControlRunManager;
}): Promise<ServerMessage[]> {
  switch (opts.command.type) {
    case "pause": {
      const expectedRunId = opts.command.client_run_id
        ? opts.runManager.getState?.().runId
        : undefined;
      if (expectedRunId === undefined) opts.runManager.pause();
      else opts.runManager.pause(expectedRunId);
      return [{ type: "ack", action: "pause", ...commandResponseMetadata(opts.command) }];
    }
    case "resume": {
      const expectedRunId = opts.command.client_run_id
        ? opts.runManager.getState?.().runId
        : undefined;
      if (expectedRunId === undefined) opts.runManager.resume();
      else opts.runManager.resume(expectedRunId);
      return [{ type: "ack", action: "resume", ...commandResponseMetadata(opts.command) }];
    }
    case "inject_hint": {
      const expectedRunId = opts.runManager.getState?.().runId;
      const imageAttachments = await validateImageAttachmentsForInference(
        opts.command.image_attachments ?? [],
      );
      if (expectedRunId !== undefined) {
        opts.runManager.injectHint(opts.command.text, imageAttachments, expectedRunId);
      } else {
        opts.runManager.injectHint(opts.command.text, imageAttachments);
      }
      return [{ type: "ack", action: "inject_hint", ...commandResponseMetadata(opts.command) }];
    }
    case "override_gate": {
      const expectedRunId = opts.runManager.getState?.().runId;
      if (expectedRunId !== undefined) {
        opts.runManager.overrideGate(opts.command.decision, expectedRunId);
      } else {
        opts.runManager.overrideGate(opts.command.decision);
      }
      return [
        {
          type: "ack",
          action: "override_gate",
          decision: opts.command.decision,
          ...commandResponseMetadata(opts.command),
        },
      ];
    }
    case "start_run": {
      const runId = await opts.runManager.startRun(
        opts.command.scenario,
        opts.command.generations,
        {
          requirePlaybookApproval: opts.command.require_playbook_approval,
        },
      );
      return [
        buildRunAcceptedMessage({
          clientRunId: opts.command.client_run_id,
          commandId: opts.command.command_id,
          runId,
          scenario: opts.command.scenario,
          generations: opts.command.generations,
        }),
      ];
    }
    case "list_scenarios":
      return [buildEnvironmentMessage(opts.runManager.getEnvironmentInfo())];
    default:
      throw new Error(
        `Unsupported interactive control command: ${String((opts.command as { type?: unknown }).type ?? "unknown")}`,
      );
  }
}

function commandResponseMetadata(command: { client_run_id?: string; command_id?: string }): {
  client_run_id?: string;
  command_id?: string;
} {
  return {
    ...(command.client_run_id ? { client_run_id: command.client_run_id } : {}),
    ...(command.command_id ? { command_id: command.command_id } : {}),
  };
}
