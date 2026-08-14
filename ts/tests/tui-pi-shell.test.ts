import { readFileSync } from "node:fs";
import { join } from "node:path";
import { describe, expect, it, vi } from "vitest";

import type { ClientMessage, ServerMessage } from "../src/server/protocol.js";
import {
  MaskedInputComponent,
  createAutoctxEditor,
  renderTuiPlanLines,
  renderTuiTranscriptLines,
  startInteractiveTui,
} from "../src/tui/app.js";
import { TuiReadModelClient } from "../src/tui/read-model-client.js";
import { TuiSession } from "../src/tui/session.js";
import {
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
