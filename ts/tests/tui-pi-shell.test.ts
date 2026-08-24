import { mkdtempSync, readFileSync, rmSync, writeFileSync } from "node:fs";
import { tmpdir } from "node:os";
import { join } from "node:path";
import { describe, expect, it, vi } from "vitest";

import type { ClientMessage, ServerMessage } from "../src/server/protocol.js";
import {
  MaskedInputComponent,
  SafeAutocompleteProvider,
  TuiTranscriptBuffer,
  createAutoctxEditor,
  isPriorityTuiSubmission,
  renderTuiPlanLines,
  renderTuiTranscriptLines,
  sanitizeTuiText,
  startInteractiveTui,
} from "../src/tui/app.js";
import { TuiReadModelClient } from "../src/tui/read-model-client.js";
import { TuiSession } from "../src/tui/session.js";
import {
  CombinedAutocompleteProvider,
  Key,
  TuiAltScreen,
  visibleWidth,
  type Terminal,
} from "../src/tui/pi-tui-adapter.js";
import type { TuiTransport, TuiTransportConnectionEvent } from "../src/tui/transport.js";
import { createInitialTuiViewModel, reduceTuiViewModel } from "../src/tui/view-model.js";

class VirtualTerminal implements Terminal {
  columns = 80;
  rows = 24;
  kittyProtocolActive = false;
  readonly writes: string[] = [];
  readonly stop = vi.fn();
  #onInput: ((data: string) => void) | null = null;
  #onResize: (() => void) | null = null;

  start(onInput: (data: string) => void, onResize: () => void): void {
    this.#onInput = onInput;
    this.#onResize = onResize;
  }
  async drainInput(): Promise<void> {}
  write(data: string): void { this.writes.push(data); }
  moveBy(): void {}
  hideCursor(): void {}
  showCursor(): void {}
  clearLine(): void {}
  clearFromCursor(): void {}
  clearScreen(): void {}
  setTitle(): void {}
  setProgress(): void {}
  input(data: string): void { this.#onInput?.(data); }
  resize(columns: number): void {
    this.columns = columns;
    this.#onResize?.();
  }
}

class FakeTransport implements TuiTransport {
  readonly endpoint = "ws://example/ws/interactive?transcript_protocol_version=1";
  readonly sent: ClientMessage[] = [];
  readonly disconnect = vi.fn();
  readonly #messages = new Set<(message: ServerMessage) => void>();
  readonly #connections = new Set<(event: TuiTransportConnectionEvent) => void>();
  async connect(): Promise<void> {
    this.connection({ status: "connected", attempt: 1 });
    this.message({
      type: "hello",
      protocol_version: 2,
      transcript_protocol_version: 1,
      capabilities: [],
    });
  }
  send(message: ClientMessage): void { this.sent.push(message); }
  onMessage(listener: (message: ServerMessage) => void): () => void {
    this.#messages.add(listener);
    return () => this.#messages.delete(listener);
  }
  onConnection(listener: (event: TuiTransportConnectionEvent) => void): () => void {
    this.#connections.add(listener);
    return () => this.#connections.delete(listener);
  }
  message(message: ServerMessage): void { for (const listener of this.#messages) listener(message); }
  connection(event: TuiTransportConnectionEvent): void { for (const listener of this.#connections) listener(event); }
}

describe("pi-tui shell", () => {
  it("pins the pi-tui and Node baselines without legacy Ink/React dependencies", () => {
    const manifest = JSON.parse(readFileSync(join(import.meta.dirname, "..", "package.json"), "utf-8")) as {
      dependencies: Record<string, string>;
      devDependencies: Record<string, string>;
      engines: { node: string };
    };
    const locks = ["package-lock.json", "bun.lock"]
      .map((name) => readFileSync(join(import.meta.dirname, "..", name), "utf-8"))
      .join("\n");

    expect(manifest.dependencies["@earendil-works/pi-tui"]).toBe("0.84.2");
    expect(manifest.engines.node).toBe(">=22.19.0");
    for (const removed of ["ink", "ink-text-input", "react", "@types/react"]) {
      expect(manifest.dependencies[removed]).toBeUndefined();
      expect(manifest.devDependencies[removed]).toBeUndefined();
      const escaped = removed.replace(/[.*+?^${}()|[\]\\]/g, "\\$&");
      expect(locks).not.toMatch(new RegExp(`[/\"]${escaped}(?:@|\")`));
    }
  });

  it("keeps renderer and command execution behind transport/read-model boundaries", () => {
    const productionModules = [
      "src/tui/app.ts",
      "src/tui/registered-command-workflow.ts",
      "src/tui/read-model-client.ts",
      "src/tui/session.ts",
    ].map((path) => readFileSync(join(import.meta.dirname, "..", path), "utf-8"));
    const boundary = readFileSync(
      join(import.meta.dirname, "..", "src/tui/pi-tui-adapter.ts"),
      "utf-8",
    );
    for (const source of productionModules) {
      expect(source).not.toMatch(/from\s+["'][^"']*(?:run-manager|storage|sqlite)[^"']*["']/i);
      expect(source).not.toContain('from "@earendil-works/pi-tui"');
    }
    expect(boundary).toContain('from "@earendil-works/pi-tui"');
  });

  it.each([40, 80, 120])("keeps Unicode transcript and plan lines within %i columns", (width) => {
    let model = createInitialTuiViewModel("ws://example/ws/interactive");
    model = reduceTuiViewModel(model, { kind: "message", message: {
      type: "event",
      event: "task_plan_updated",
      event_id: "plan-1",
      sequence: 1,
      payload: {
        run_id: "run-1",
        plan_id: "plan-1",
        version: 1,
        plan_revision: 1,
        update_kind: "initial",
        active_step_id: "step-1",
        summary: "検証する e\u0301 and wrap a deliberately long streaming Markdown summary",
        steps: [{ id: "step-1", label: "日本語の幅を検証する", detail: "combining e\u0301", status: "in_progress" }],
      },
    } });
    for (const line of [
      ...renderTuiTranscriptLines(model, ["operator · **streaming** 日本語 e\u0301"], width),
      ...renderTuiPlanLines(model, width),
    ]) {
      expect(visibleWidth(line)).toBeLessThanOrEqual(width);
    }
  });

  it("keeps semantic and operator output in one arrival-ordered transcript", () => {
    const buffer = new TuiTranscriptBuffer();
    let model = createInitialTuiViewModel("ws://example/ws/interactive");
    model = reduceTuiViewModel(model, { kind: "message", message: {
      type: "event",
      event: "generation_started",
      event_id: "event-1",
      sequence: 1,
      payload: { generation: 1 },
    } });
    buffer.setModel(model);
    buffer.appendOperatorLines(["operator help output"]);
    model = reduceTuiViewModel(model, { kind: "message", message: {
      type: "event",
      event: "generation_completed",
      event_id: "event-2",
      sequence: 2,
      payload: { generation: 1 },
    } });
    buffer.setModel(model);

    const rendered = buffer.render(120).join("\n");
    expect(rendered.indexOf("Generation started")).toBeLessThan(rendered.indexOf("operator help output"));
    expect(rendered.indexOf("operator help output")).toBeLessThan(rendered.indexOf("Generation completed"));
  });

  it("clears presentation history when the attached client run changes", () => {
    const buffer = new TuiTranscriptBuffer();
    let model = createInitialTuiViewModel("ws://example/ws/interactive");
    model = reduceTuiViewModel(model, { kind: "message", message: {
      type: "run_accepted",
      run_id: "run-1",
      scenario: "grid_ctf",
      generations: 1,
      client_run_id: "client-1",
      sequence: 1,
    } });
    buffer.setModel(model);
    model = reduceTuiViewModel(model, { kind: "message", message: {
      type: "event",
      event: "run_completed",
      event_id: "run-1-complete",
      payload: { run_id: "run-1" },
      client_run_id: "client-1",
      sequence: 2,
    } });
    buffer.setModel(model);
    model = reduceTuiViewModel(model, { kind: "message", message: {
      type: "run_accepted",
      run_id: "run-2",
      scenario: "othello",
      generations: 1,
      client_run_id: "client-2",
      sequence: 1,
    } });
    buffer.setModel(model);

    const rendered = buffer.render(120).join("\n");
    expect(rendered).toContain("othello");
    expect(rendered).not.toContain("grid_ctf");
    expect(rendered).not.toContain("Run completed");
  });

  it("keeps chat content visible when activity verbosity is quiet", () => {
    const buffer = new TuiTranscriptBuffer();
    let model = createInitialTuiViewModel("ws://example/ws/interactive");
    model = reduceTuiViewModel(model, { kind: "message", message: {
      type: "chat_response",
      role: "architect",
      text: "the actual answer",
    } });
    buffer.setModel(model);
    buffer.setActivitySettings({ filter: "all", verbosity: "quiet" });
    expect(buffer.render(120).join("\n")).toContain("the actual answer");
  });

  it("applies activity filters only to detail rows and keeps lifecycle rows visible", () => {
    const buffer = new TuiTranscriptBuffer();
    let model = createInitialTuiViewModel("ws://example/ws/interactive");
    model = reduceTuiViewModel(model, { kind: "message", message: {
      type: "event",
      event: "generation_started",
      event_id: "lifecycle",
      sequence: 1,
      payload: { generation: 1 },
    } });
    model = reduceTuiViewModel(model, { kind: "message", message: {
      type: "event",
      event: "runtime_session_event",
      event_id: "prompt",
      sequence: 2,
      payload: {
        session_id: "prompt-session",
        event: { event_type: "prompt_submitted", sequence: 1, payload: {} },
      },
    } });
    model = reduceTuiViewModel(model, { kind: "message", message: {
      type: "event",
      event: "runtime_session_event",
      event_id: "command",
      sequence: 3,
      payload: {
        session_id: "command-session",
        event: { event_type: "shell_command", sequence: 2, payload: {} },
      },
    } });
    buffer.setModel(model);
    buffer.setActivitySettings({ filter: "commands", verbosity: "normal" });

    const rendered = buffer.render(120).join("\n");
    expect(rendered).toContain("Generation started");
    expect(rendered).not.toContain("prompt-session");
    expect(rendered).toContain("command-session");
  });

  it("strips untrusted terminal controls and redacts credentials before adding safe links", () => {
    expect(sanitizeTuiText("before\u001b]52;c;c2VjcmV0\u0007after\u001b[2J"))
      .toBe("beforeafter");
    expect(sanitizeTuiText("see https://user:pass@example.com]"))
      .not.toContain("user:pass");
    expect(sanitizeTuiText("https://bad[/?%74oken=sekrit"))
      .not.toContain("sekrit");
    expect(sanitizeTuiText("https://host/cb#access%5Ftoken=sekrit"))
      .not.toContain("sekrit");
    expect(sanitizeTuiText("https://host/cb#%74oken=sekrit"))
      .not.toContain("sekrit");
    const redactedCredentials = sanitizeTuiText([
      "Authorization: Bearer bearer-credential",
      "Authorization: Basic QWxhZGRpbjpvcGVuIHNlc2FtZQ==",
      '{"api_key": "api value with spaces", "token": "token value with spaces"}',
      'password="correct horse battery staple"',
      "OPENAI_API_KEY=sk-supersecret",
      "GITHUB_TOKEN=github-secret",
      "auth=plain-auth bearer=plain-bearer",
    ].join("\n"));
    expect(redactedCredentials).not.toContain("bearer-credential");
    expect(redactedCredentials).not.toContain("QWxhZGRpbjpvcGVuIHNlc2FtZQ==");
    expect(redactedCredentials).not.toContain("api value with spaces");
    expect(redactedCredentials).not.toContain("token value with spaces");
    expect(redactedCredentials).not.toContain("correct horse battery staple");
    expect(redactedCredentials).not.toContain("sk-supersecret");
    expect(redactedCredentials).not.toContain("github-secret");
    expect(redactedCredentials).not.toContain("plain-auth");
    expect(redactedCredentials).not.toContain("plain-bearer");
    const redactedUrl = sanitizeTuiText(
      "https://host.example/path?auth=auth-value&bearer=bearer-value&region=us",
    );
    expect(redactedUrl).not.toContain("auth-value");
    expect(redactedUrl).not.toContain("bearer-value");
    expect(redactedUrl).toContain("auth=REDACTED&bearer=REDACTED&region=us");
    const model = {
      ...createInitialTuiViewModel("ws://example/ws/interactive"),
      transcript: [{
        id: "malicious",
        event: "chat_response",
        kind: "message" as const,
        tone: "normal" as const,
        title: "agent",
        detail: "\u001b]52;c;c2VjcmV0\u0007https://user:pass@example.test/result?token=private",
        activity: { family: "run" as const, focus: "run" as const, hasError: false },
      }],
    };
    const rendered = renderTuiTranscriptLines(model, [], 160).join("\n");
    expect(rendered).not.toContain("\u001b]52");
    expect(rendered).not.toContain("user:pass");
    expect(rendered).not.toContain("private");
    expect(rendered).toContain("REDACTED");
    expect(rendered).toContain("\u001b]8;;https://example.test/result?token=REDACTED");
  });

  it("strips large unterminated terminal strings in linear time", () => {
    const malicious = "\u001b]".repeat(50_000);
    const started = performance.now();
    expect(sanitizeTuiText(malicious)).toBe("");
    expect(performance.now() - started).toBeLessThan(500);
  });

  it("masks paste and backspace input and never renders the secret", () => {
    const input = new MaskedInputComponent("API key");
    input.focused = true;
    input.handleInput("\u001b[200~sk-secret-日本語\u001b[201~");
    input.handleInput("\u007f");
    const rendered = input.render(80).join("\n");
    expect(rendered).not.toContain("sk-secret");
    expect(rendered).toContain("••••");
    let submitted = "";
    input.onSubmit = (value) => { submitted = value; };
    input.handleInput("\r");
    expect(submitted).toBe("sk-secret-日本");
    expect(input.render(80).join("\n")).not.toContain(submitted);
  });

  it("ignores navigation and delete escape sequences in masked input", () => {
    const input = new MaskedInputComponent("API key");
    input.focused = true;
    input.handleInput("secret");
    input.handleInput("\u001b[A");
    input.handleInput("\u001b[3~");
    input.handleInput("\u009bA");
    input.handleInput("\u001b[200~ignored\u009bA\u001b[201~");
    let submitted = "";
    input.onSubmit = (value) => { submitted = value; };
    input.handleInput("\r");
    expect(submitted).toBe("secret");
  });

  it("uses registry completion and persistent in-session editor history", async () => {
    const terminal = new VirtualTerminal();
    const tui = new TuiAltScreen(terminal, true, "/tmp/autoctx-tui-test");
    const editor = createAutoctxEditor(tui, ["safe_run_stop_v1"]);
    editor.focused = true;
    editor.addToHistory("/status run-1");
    editor.setText("");
    editor.handleInput("\u001b[A");
    expect(editor.getText()).toBe("/status run-1");
    editor.setText("/he");
    editor.handleInput("\t");
    await vi.waitFor(() => expect(editor.isShowingAutocomplete()).toBe(true));
    editor.handleInput("\t");
    await vi.waitFor(() => expect(editor.getText()).toBe("/help "));
    editor.setText("");
    editor.handleInput("\u001b[200~first\nsecond\u001b[201~");
    expect(editor.getExpandedText()).toContain("first\nsecond");
    editor.setText("");
    editor.handleInput("\u001b[200~before\u009d52;c;c2VjcmV0\u009cafter\u001b[201~");
    expect(editor.getExpandedText()).toBe("");
    editor.handleInput("\u009bA");
    expect(editor.getExpandedText()).toBe("");
    editor.handleInput("before\u001b]52;c;c2VjcmV0\u0007after");
    editor.handleInput("before\u001bPmalicious\u001b\\after");
    editor.handleInput("before\u001b[2Jafter");
    expect(editor.getExpandedText()).toBe("");
    expect(editor.render(100).join("\n")).not.toMatch(/[\u009b\u009d\u009c]/);
  });

  it("filters terminal controls from filesystem autocomplete labels and values", async () => {
    const directory = mkdtempSync(join(tmpdir(), "autoctx-autocomplete-"));
    try {
      writeFileSync(join(directory, "safe.txt"), "safe");
      writeFileSync(join(directory, "evil\u001b]52;c;c2VjcmV0\u0007.txt"), "unsafe");
      const provider = new SafeAutocompleteProvider(
        new CombinedAutocompleteProvider([], directory),
      );
      const suggestions = await provider.getSuggestions([""], 0, 0, {
        signal: new AbortController().signal,
        force: true,
      });

      expect(suggestions?.items.some((item) => item.value.includes("safe.txt"))).toBe(true);
      expect(suggestions?.items).not.toEqual(expect.arrayContaining([
        expect.objectContaining({ label: expect.stringMatching(/[\u0000-\u001f\u007f-\u009f]/) }),
      ]));
      const blocked = provider.applyCompletion(
        ["@"],
        0,
        1,
        { value: "@evil\u001b]52;c;c2VjcmV0\u0007.txt", label: "evil" },
        "@",
      );
      expect(blocked).toEqual({ lines: ["@"], cursorLine: 0, cursorCol: 1 });
    } finally {
      rmSync(directory, { recursive: true, force: true });
    }
  });

  it("recognizes stop as the only out-of-band slash-command family", () => {
    expect(isPriorityTuiSubmission("/stop")).toBe(true);
    expect(isPriorityTuiSubmission("  /STOP confirm  ")).toBe(true);
    expect(isPriorityTuiSubmission("/chat analyst stop")).toBe(false);
    expect(isPriorityTuiSubmission("/status")).toBe(false);
  });

  it("coalesces event bursts, survives resize/search input, and cleans terminal state", async () => {
    const terminal = new VirtualTerminal();
    const transport = new FakeTransport();
    const session = new TuiSession(transport);
    const handle = startInteractiveTui({
      session,
      readModels: new TuiReadModelClient(transport.endpoint, {
        fetchImpl: vi.fn() as unknown as typeof fetch,
      }),
      terminal,
      logDirectory: "/tmp/autoctx-tui-test",
    });
    await vi.waitFor(() => expect(session.viewModel.connection.status).toBe("connected"));
    for (let index = 1; index <= 100; index += 1) {
      transport.message({
        type: "event",
        event: "generation_started",
        payload: { generation: index },
        event_id: `burst-${index}`,
        sequence: index,
      });
    }
    terminal.resize(40);
    terminal.resize(80);
    terminal.resize(120);
    terminal.input("\u001b[5~");
    terminal.input("\u001b[1;6F");
    await new Promise((resolve) => setTimeout(resolve, 30));
    expect(session.viewModel.transcript).toHaveLength(100);
    expect(terminal.writes.length).toBeLessThan(100);
    handle.stop();
    await expect(handle.done).resolves.toBeUndefined();
    expect(terminal.stop).toHaveBeenCalledOnce();
    expect(transport.disconnect).toHaveBeenCalledOnce();
  });
});
