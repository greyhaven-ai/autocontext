import { randomUUID } from "node:crypto";

import {
  IMAGE_ATTACHMENTS_CAPABILITY,
  type ClientMessage,
  type ImageAttachment,
  type ServerMessage,
} from "../server/protocol.js";
import {
  createInitialTuiViewModel,
  reduceTuiViewModel,
  type TuiViewModel,
} from "./view-model.js";
import type { TuiTransport } from "./transport.js";

interface PendingResponse {
  readonly commandId?: string;
  readonly accept: (message: ServerMessage) => boolean;
  readonly resolve: (message: ServerMessage) => void;
  readonly reject: (error: Error) => void;
  readonly timeout: ReturnType<typeof setTimeout>;
}

export class TuiSession {
  readonly transport: TuiTransport;
  readonly #listeners = new Set<(model: TuiViewModel) => void>();
  readonly #pending = new Set<PendingResponse>();
  readonly #cleanup: Array<() => void> = [];
  #model: TuiViewModel;
  #commandTail: Promise<void> = Promise.resolve();
  #connectionEpoch = 0;
  #resumedEpoch = -1;
  #started = false;

  constructor(transport: TuiTransport) {
    this.transport = transport;
    this.#model = createInitialTuiViewModel(transport.endpoint);
    this.#cleanup.push(
      transport.onConnection((event) => {
        if (event.status === "connected") {
          this.#connectionEpoch += 1;
          this.#resumedEpoch = -1;
        }
        this.#reduce({
          kind: "connection",
          status: event.status,
          attempt: event.attempt,
          error: event.error,
        });
      }),
      transport.onMessage((message) => this.#handleMessage(message)),
    );
  }

  get viewModel(): TuiViewModel {
    return this.#model;
  }

  get isBusy(): boolean {
    return this.#model.busyCommandId !== null;
  }

  async start(): Promise<void> {
    if (this.#started) return;
    this.#started = true;
    await this.transport.connect();
  }

  close(): void {
    this.transport.disconnect();
    for (const pending of this.#pending) {
      clearTimeout(pending.timeout);
      pending.reject(new Error("TUI session detached"));
    }
    this.#pending.clear();
    for (const cleanup of this.#cleanup.splice(0)) cleanup();
    this.#started = false;
  }

  subscribe(listener: (model: TuiViewModel) => void): () => void {
    this.#listeners.add(listener);
    listener(this.#model);
    return () => this.#listeners.delete(listener);
  }

  async startRun(scenario: string, iterations: number): Promise<string> {
    if (!Number.isInteger(iterations) || iterations <= 0) {
      throw new Error("iterations must be a positive integer");
    }
    return this.#serialize("start_run", async (commandId) => {
      const clientRunId = `tui-${randomUUID()}`;
      const response = await this.#request({
        type: "start_run",
        scenario,
        generations: iterations,
        require_playbook_approval: false,
        client_run_id: clientRunId,
        command_id: commandId,
      }, (message) => message.type === "run_accepted" && message.command_id === commandId);
      if (response.type !== "run_accepted") throw new Error("Run was not accepted");
      return response.run_id;
    });
  }

  async pause(): Promise<void> {
    await this.#runScopedAck("pause");
  }

  async resume(): Promise<void> {
    await this.#runScopedAck("resume");
  }

  async injectHint(text: string, imageAttachments: readonly ImageAttachment[] = []): Promise<void> {
    if (!text.trim()) throw new Error("hint text is required");
    this.#assertImageCapability(imageAttachments);
    await this.#serialize("inject_hint", async (commandId) => {
      const clientRunId = this.#requireActiveClientRunId();
      await this.#request({
        type: "inject_hint",
        text,
        ...(imageAttachments.length ? { image_attachments: [...imageAttachments] } : {}),
        client_run_id: clientRunId,
        command_id: commandId,
      }, ackFor(commandId, "inject_hint"));
    });
  }

  async overrideGate(decision: "advance" | "retry" | "rollback"): Promise<void> {
    await this.#serialize("override_gate", async (commandId) => {
      const clientRunId = this.#requireActiveClientRunId();
      await this.#request({
        type: "override_gate",
        decision,
        client_run_id: clientRunId,
        command_id: commandId,
      }, ackFor(commandId, "override_gate"));
    });
  }

  async stopActiveRun(): Promise<string> {
    if (!this.#model.capabilities.includes("safe_run_stop_v1")) {
      throw new Error("The connected server does not advertise safe run stopping");
    }
    return this.#serialize("stop", async (commandId) => {
      const clientRunId = this.#requireActiveClientRunId();
      const response = await this.#request({
        type: "stop",
        client_run_id: clientRunId,
        command_id: commandId,
      }, ackFor(commandId, "stop"));
      return response.type === "ack" ? response.decision ?? "requested" : "requested";
    });
  }

  async chat(
    role: string,
    message: string,
    imageAttachments: readonly ImageAttachment[] = [],
  ): Promise<string> {
    if (!message.trim()) throw new Error("chat message is required");
    this.#assertImageCapability(imageAttachments);
    return this.#serialize("chat_agent", async (commandId) => {
      const clientRunId = this.#model.run.clientRunId ?? undefined;
      const response = await this.#request({
        type: "chat_agent",
        role,
        message,
        ...(imageAttachments.length ? { image_attachments: [...imageAttachments] } : {}),
        ...(clientRunId ? { client_run_id: clientRunId } : {}),
        command_id: commandId,
      }, (candidate) => candidate.type === "chat_response" && candidate.command_id === commandId);
      if (response.type !== "chat_response") throw new Error("Agent chat did not return a response");
      return response.text;
    });
  }

  async createScenario(description: string): Promise<{ name: string }> {
    return this.#serialize("create_scenario", async () => {
      const response = await this.#request(
        { type: "create_scenario", description },
        (message) => message.type === "scenario_preview",
      );
      if (response.type !== "scenario_preview") throw new Error("Scenario preview unavailable");
      return { name: response.name };
    });
  }

  async confirmScenario(): Promise<{ name: string }> {
    return this.#serialize("confirm_scenario", async () => {
      const response = await this.#request(
        { type: "confirm_scenario" },
        (message) => message.type === "scenario_ready",
      );
      if (response.type !== "scenario_ready") throw new Error("Scenario was not confirmed");
      return { name: response.name };
    });
  }

  async login(provider: string, apiKey?: string, model?: string, baseUrl?: string): Promise<void> {
    await this.#serialize("login", async () => {
      const response = await this.#request({
        type: "login",
        provider,
        ...(apiKey ? { apiKey } : {}),
        ...(model ? { model } : {}),
        ...(baseUrl ? { baseUrl } : {}),
      }, (message) => message.type === "auth_status");
      if (response.type !== "auth_status" || !response.authenticated) {
        throw new Error(`Unable to authenticate ${provider}`);
      }
    });
  }

  async logout(provider?: string): Promise<void> {
    await this.#serialize("logout", async () => {
      await this.#request(
        { type: "logout", ...(provider ? { provider } : {}) },
        (message) => message.type === "auth_status",
      );
    });
  }

  async switchProvider(provider: string): Promise<void> {
    await this.#serialize("switch_provider", async () => {
      await this.#request(
        { type: "switch_provider", provider },
        (message) => message.type === "auth_status",
      );
    });
  }

  async whoami(): Promise<Extract<ServerMessage, { type: "auth_status" }>> {
    return this.#serialize("whoami", async () => {
      const response = await this.#request(
        { type: "whoami" },
        (message) => message.type === "auth_status",
      );
      if (response.type !== "auth_status") throw new Error("Authentication status unavailable");
      return response;
    });
  }

  resolvePendingDecision(scenario: string): void {
    this.#reduce({ kind: "decision_resolved", scenario });
  }

  #runScopedAck(action: "pause" | "resume"): Promise<void> {
    return this.#serialize(action, async (commandId) => {
      const clientRunId = this.#requireActiveClientRunId();
      await this.#request(
        { type: action, client_run_id: clientRunId, command_id: commandId },
        ackFor(commandId, action),
      );
    });
  }

  #request(
    message: ClientMessage,
    accept: (message: ServerMessage) => boolean,
    timeoutMs = 120_000,
  ): Promise<ServerMessage> {
    return new Promise<ServerMessage>((resolve, reject) => {
      const pending: PendingResponse = {
        ...(readClientCommandId(message) ? { commandId: readClientCommandId(message) } : {}),
        accept,
        resolve,
        reject,
        timeout: setTimeout(() => {
          this.#pending.delete(pending);
          reject(new Error(`Timed out waiting for '${message.type}' response`));
        }, timeoutMs),
      };
      pending.timeout.unref?.();
      this.#pending.add(pending);
      try {
        this.transport.send(message);
      } catch (error) {
        clearTimeout(pending.timeout);
        this.#pending.delete(pending);
        reject(error instanceof Error ? error : new Error(String(error)));
      }
    });
  }

  #serialize<T>(action: string, execute: (commandId: string) => Promise<T>): Promise<T> {
    const commandId = `${action}-${randomUUID()}`;
    let resolveResult!: (result: T) => void;
    let rejectResult!: (error: unknown) => void;
    const result = new Promise<T>((resolve, reject) => {
      resolveResult = resolve;
      rejectResult = reject;
    });
    this.#commandTail = this.#commandTail.then(async () => {
      this.#reduce({ kind: "busy", commandId });
      try {
        resolveResult(await execute(commandId));
      } catch (error) {
        rejectResult(error);
      } finally {
        this.#reduce({ kind: "busy", commandId: null });
      }
    });
    return result;
  }

  #handleMessage(message: ServerMessage): void {
    this.#reduce({ kind: "message", message });
    for (const pending of [...this.#pending]) {
      if (
        message.type === "error" &&
        message.command_id &&
        pending.commandId === message.command_id
      ) {
        clearTimeout(pending.timeout);
        this.#pending.delete(pending);
        pending.reject(new Error(message.message));
        continue;
      }
      if (!pending.accept(message)) continue;
      clearTimeout(pending.timeout);
      this.#pending.delete(pending);
      pending.resolve(message);
    }
    if (message.type === "state") this.#resumeTranscriptIfNeeded();
  }

  #resumeTranscriptIfNeeded(): void {
    const clientRunId = this.#model.run.clientRunId;
    if (
      !clientRunId ||
      this.#resumedEpoch === this.#connectionEpoch ||
      this.#model.connection.status !== "connected" ||
      !this.#model.capabilities.includes("run_transcript_v1")
    ) return;
    this.#resumedEpoch = this.#connectionEpoch;
    try {
      this.transport.send({
        type: "resume_run",
        client_run_id: clientRunId,
        after_sequence: this.#model.lastSequence,
        command_id: `resume_run-${randomUUID()}`,
      });
    } catch {
      this.#resumedEpoch = -1;
    }
  }

  #requireActiveClientRunId(): string {
    if (!this.#model.run.active || !this.#model.run.clientRunId) {
      throw new Error("No active run is attached");
    }
    return this.#model.run.clientRunId;
  }

  #assertImageCapability(attachments: readonly ImageAttachment[]): void {
    if (attachments.length && !this.#model.capabilities.includes(IMAGE_ATTACHMENTS_CAPABILITY)) {
      throw new Error("The active provider/model path does not support image attachments");
    }
  }

  #reduce(input: Parameters<typeof reduceTuiViewModel>[1]): void {
    const next = reduceTuiViewModel(this.#model, input);
    if (next === this.#model) return;
    this.#model = next;
    for (const listener of [...this.#listeners]) listener(next);
  }
}

function readClientCommandId(message: ClientMessage): string | undefined {
  return "command_id" in message && typeof message.command_id === "string"
    ? message.command_id
    : undefined;
}

function ackFor(commandId: string, action: string): (message: ServerMessage) => boolean {
  return (message) =>
    (message.type === "ack" && message.command_id === commandId && message.action === action) ||
    (message.type === "error" && message.command_id === commandId);
}
