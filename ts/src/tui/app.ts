import {
  AUTOCTX_EDITOR_THEME,
  CombinedAutocompleteProvider,
  Editor,
  Key,
  ProcessTerminal,
  ScrollView,
  TuiAltScreen,
  VStack,
  matchesKey,
  truncateToWidth,
  wrapTextWithAnsi,
  type Component,
  type Focusable,
  type Terminal,
  type TUI,
} from "./pi-tui-adapter.js";
import { formatTuiCommandHelp, tuiSlashCommands } from "./command-registry.js";
import { TuiCommandRuntime, type TuiSecretRequest } from "./registered-command-workflow.js";
import type { TuiReadModelClient } from "./read-model-client.js";
import type { TuiSession } from "./session.js";
import type { TuiTranscriptRow, TuiViewModel } from "./view-model.js";

export interface InteractiveTuiOptions {
  readonly session: TuiSession;
  readonly readModels: TuiReadModelClient;
  readonly terminal?: Terminal;
  readonly logDirectory: string;
}

export interface InteractiveTuiHandle {
  readonly done: Promise<void>;
  stop(): void;
}

/**
 * Start the alternate-screen operator UI. Local and remote callers both pass
 * the same WebSocket-backed session; no renderer component knows RunManager or SQLite.
 */
export function startInteractiveTui(options: InteractiveTuiOptions): InteractiveTuiHandle {
  const terminal = options.terminal ?? new ProcessTerminal();
  const tui = new TuiAltScreen(terminal, true, options.logDirectory, {
    mouse: true,
    wheelScrollLines: 3,
  });
  const transcript = new TranscriptComponent();
  const scrollView = new ScrollView(transcript, {
    follow: "end",
    primary: true,
    overscroll: "chain",
    scrollbar: "auto",
  });
  const header = new HeaderComponent(options.session.viewModel);
  const plan = new PlanComponent(options.session.viewModel);
  const footer = new FooterComponent(options.session.viewModel);
  const editor = createAutoctxEditor(tui, options.session.viewModel.capabilities);
  const layout = new VStack([
    { component: header, basis: "auto", minSize: 2 },
    {
      component: plan,
      basis: "auto",
      maxSize: 8,
      visible: (viewport) => viewport.width >= 80 && plan.hasContent,
    },
    { component: scrollView, basis: 0, grow: 1, minSize: 3 },
    { component: footer, basis: "auto", minSize: 1 },
    { component: editor, basis: "auto", shrink: 1, minSize: 1, maxSize: 8 },
  ]);
  tui.setLayoutRoot(layout);

  let settled = false;
  let resolveDone!: () => void;
  let rejectDone!: (error: Error) => void;
  const done = new Promise<void>((resolve, reject) => {
    resolveDone = resolve;
    rejectDone = reject;
  });
  const runtime = new TuiCommandRuntime({
    session: options.session,
    readModels: options.readModels,
    onAsyncLines: (lines) => {
      transcript.appendOperatorLines(lines);
      tui.requestRender();
    },
  });
  let secretOverlay: ReturnType<TUI["showOverlay"]> | null = null;
  let activeSecretInput: MaskedInputComponent | null = null;
  let autocompleteCapabilities = options.session.viewModel.capabilities.join("\u0000");
  let unsubscribe: () => void = () => {};
  let removeInputListener: () => void = () => {};

  const cleanup = (error?: unknown) => {
    if (settled) return;
    settled = true;
    runtime.detach();
    activeSecretInput?.clear();
    activeSecretInput = null;
    secretOverlay?.hide();
    secretOverlay = null;
    unsubscribe();
    removeInputListener();
    options.session.close();
    try {
      // pi-tui restores the main screen and prints the complete final document.
      tui.stop({ preserveScreen: false });
    } catch (stopError) {
      error ??= stopError;
    }
    if (error) rejectDone(error instanceof Error ? error : new Error(String(error)));
    else resolveDone();
  };

  const openSecretPrompt = (request: TuiSecretRequest) => {
    const prompt = new MaskedInputComponent(`API key for ${request.provider}`);
    activeSecretInput = prompt;
    prompt.onCancel = () => {
      prompt.clear();
      activeSecretInput = null;
      secretOverlay?.hide();
      secretOverlay = null;
      tui.setFocus(editor);
      transcript.appendOperatorLines(["credential prompt cancelled"]);
      tui.requestRender();
    };
    prompt.onSubmit = (secret) => {
      prompt.clear();
      activeSecretInput = null;
      secretOverlay?.hide();
      secretOverlay = null;
      tui.setFocus(editor);
      void runtime.submitSecret(request, secret).then(
        (lines) => transcript.appendOperatorLines(lines),
        (error) => transcript.appendOperatorLines([`error: ${errorMessage(error)}`]),
      ).finally(() => tui.requestRender());
    };
    secretOverlay = tui.showOverlay(prompt, {
      width: "70%",
      maxHeight: 5,
      anchor: "bottom-center",
      margin: 1,
    });
    secretOverlay.focus();
  };

  editor.onSubmit = (raw) => {
    const submitted = raw;
    editor.setText("");
    if (submitted.trim()) editor.addToHistory(submitted);
    void runtime.execute(submitted).then((result) => {
      transcript.appendOperatorLines(result.lines);
      if (result.requestSecret) openSecretPrompt(result.requestSecret);
      if (result.shouldExit) cleanup();
    }, (error) => {
      transcript.appendOperatorLines([`error: ${errorMessage(error)}`]);
    }).finally(() => tui.requestRender());
  };

  unsubscribe = options.session.subscribe((model) => {
    header.setModel(model);
    plan.setModel(model);
    footer.setModel(model);
    transcript.setModel(model);
    editor.disableSubmit = model.busyCommandId !== null;
    const nextCapabilities = model.capabilities.join("\u0000");
    if (nextCapabilities !== autocompleteCapabilities) {
      autocompleteCapabilities = nextCapabilities;
      editor.setAutocompleteProvider(new CombinedAutocompleteProvider(
        tuiSlashCommands(model.capabilities),
        process.cwd(),
      ));
    }
    tui.requestRender();
  });
  removeInputListener = tui.addInputListener((data) => {
    if (matchesKey(data, Key.ctrl("c"))) {
      cleanup();
      return { consume: true };
    }
    if (matchesKey(data, "f1")) {
      transcript.appendOperatorLines(formatTuiCommandHelp(
        options.session.viewModel.capabilities,
      ));
      tui.requestRender();
      return { consume: true };
    }
    return undefined;
  });

  try {
    tui.setFocus(editor);
    tui.start();
    void options.session.start().catch(cleanup);
  } catch (error) {
    cleanup(error);
  }

  return { done, stop: () => cleanup() };
}

export function createAutoctxEditor(tui: TUI, capabilities: readonly string[]): Editor {
  const editor = new Editor(tui, AUTOCTX_EDITOR_THEME, {
    paddingX: 1,
    autocompleteMaxVisible: 10,
  });
  editor.setAutocompleteProvider(new CombinedAutocompleteProvider(
    tuiSlashCommands(capabilities),
    process.cwd(),
  ));
  return editor;
}

abstract class ModelComponent implements Component {
  protected model: TuiViewModel;

  constructor(model: TuiViewModel) {
    this.model = model;
  }

  setModel(model: TuiViewModel): void {
    this.model = model;
    this.invalidate();
  }

  invalidate(): void {}
  abstract render(width: number): string[];
}

class HeaderComponent extends ModelComponent {
  render(width: number): string[] {
    return fitLines([
      "autocontext · agent operator TUI",
      `${this.model.connection.status} · ${this.model.endpoint} · run=${this.model.run.runId ?? "none"} · scenario=${this.model.run.scenario ?? "none"}`,
    ], width);
  }
}

class FooterComponent extends ModelComponent {
  render(width: number): string[] {
    const runState = this.model.run.active
      ? `${this.model.run.paused ? "paused" : "running"} · gen ${this.model.run.generation ?? "?"} · ${this.model.run.phase ?? "waiting"}`
      : this.model.run.outcome ?? "idle";
    const busy = this.model.busyCommandId ? ` · pending ${this.model.busyCommandId.split("-")[0]}` : "";
    return fitLines([
      `${runState}${busy} · ${this.model.connection.status} · /help · Ctrl+C detaches`,
    ], width);
  }
}

class PlanComponent extends ModelComponent {
  get hasContent(): boolean {
    return this.model.taskPlan !== null;
  }

  render(width: number): string[] {
    return renderTuiPlanLines(this.model, width);
  }
}

class TranscriptComponent implements Component {
  #model: TuiViewModel | null = null;
  readonly #operatorLines: string[] = [];

  setModel(model: TuiViewModel): void {
    this.#model = model;
  }

  appendOperatorLines(lines: readonly string[]): void {
    this.#operatorLines.push(...lines);
  }

  invalidate(): void {}

  render(width: number): string[] {
    return renderTuiTranscriptLines(this.#model, this.#operatorLines, width);
  }
}

export class MaskedInputComponent implements Component, Focusable {
  focused = false;
  onSubmit?: (secret: string) => void;
  onCancel?: () => void;
  #secret = "";
  readonly #label: string;

  constructor(label: string) {
    this.#label = label;
  }

  invalidate(): void {}

  clear(): void {
    this.#secret = "";
  }

  render(width: number): string[] {
    const bullets = "•".repeat([...this.#secret].length);
    return fitLines([
      this.#label,
      `> ${bullets}${this.focused ? "_" : ""}`,
      "Enter submits · Escape cancels · value is never logged or added to history",
    ], width);
  }

  handleInput(data: string): void {
    if (matchesKey(data, Key.escape) || matchesKey(data, Key.ctrl("c"))) {
      this.#secret = "";
      this.onCancel?.();
      return;
    }
    if (matchesKey(data, Key.enter)) {
      const secret = this.#secret;
      this.#secret = "";
      this.onSubmit?.(secret);
      return;
    }
    if (matchesKey(data, Key.backspace)) {
      this.#secret = [...this.#secret].slice(0, -1).join("");
      return;
    }
    const printable = data
      .replace(/\u001b\[200~/g, "")
      .replace(/\u001b\[201~/g, "")
      .replace(/[\u0000-\u001f\u007f]/g, "");
    if (printable) this.#secret = `${this.#secret}${printable}`.slice(0, 16_384);
  }
}

export function renderTuiTranscriptLines(
  model: TuiViewModel | null,
  operatorLines: readonly string[],
  width: number,
): string[] {
  const semantic = model?.transcript.flatMap(formatTranscriptRow) ?? [];
  const lines = [...semantic, ...operatorLines];
  return lines.length
    ? fitLines(lines, width)
    : [truncateToWidth("Waiting for durable transcript events…", Math.max(1, width))];
}

export function renderTuiPlanLines(model: TuiViewModel, width: number): string[] {
  const plan = model.taskPlan;
  if (!plan) return [];
  return fitLines([
    `Plan · revision ${plan.plan_revision} · ${plan.update_kind}${plan.summary ? ` · ${plan.summary}` : ""}`,
    ...plan.steps.map((step) =>
      `  ${planStatusIcon(step.status)} ${step.label}${step.detail ? ` — ${step.detail}` : ""}`),
  ], width).slice(0, 8);
}

function formatTranscriptRow(row: TuiTranscriptRow): string[] {
  const prefix = row.sequence === undefined ? "" : `${row.sequence.toString().padStart(4)} `;
  return [
    `${prefix}${toneIcon(row.tone)} ${row.title}`,
    ...(row.detail ? row.detail.split("\n").map((line) => `     ${line}`) : []),
  ];
}

function fitLines(lines: readonly string[], width: number): string[] {
  const safeWidth = Math.max(1, width);
  return lines.flatMap((line) => {
    const wrapped = wrapTextWithAnsi(line, safeWidth);
    return (wrapped.length ? wrapped : [""]).map((part) => truncateToWidth(part, safeWidth, ""));
  });
}

function toneIcon(tone: TuiTranscriptRow["tone"]): string {
  if (tone === "success") return "✓";
  if (tone === "warning") return "!";
  if (tone === "error") return "×";
  if (tone === "muted") return "·";
  return "›";
}

function planStatusIcon(status: string): string {
  if (status === "completed") return "✓";
  if (status === "in_progress") return "→";
  if (status === "blocked") return "!";
  return "·";
}

function errorMessage(error: unknown): string {
  return error instanceof Error ? error.message : String(error);
}
