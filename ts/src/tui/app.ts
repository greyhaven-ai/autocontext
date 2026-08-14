import {
  AUTOCTX_EDITOR_THEME,
  CombinedAutocompleteProvider,
  Editor,
  hyperlink,
  Key,
  ProcessTerminal,
  ScrollView,
  TuiAltScreen,
  VStack,
  matchesKey,
  truncateToWidth,
  wrapTextWithAnsi,
  type AutocompleteItem,
  type AutocompleteProvider,
  type AutocompleteSuggestions,
  type Component,
  type Focusable,
  type Terminal,
  type TUI,
} from "./pi-tui-adapter.js";
import {
  REDACTED_PRESENTATION_VALUE,
  redactPresentationText,
} from "../security/presentation-redaction.js";
import { formatTuiCommandHelp, tuiSlashCommands } from "./command-registry.js";
import { TuiCommandRuntime, type TuiSecretRequest } from "./registered-command-workflow.js";
import type { TuiReadModelClient } from "./read-model-client.js";
import type { TuiSession } from "./session.js";
import {
  DEFAULT_TUI_ACTIVITY_SETTINGS,
  type TuiActivitySettings,
} from "./activity-summary.js";
import { displayTuiEndpoint } from "./transport.js";
import type { TuiTranscriptRow, TuiViewModel } from "./view-model.js";

const MAX_RENDERED_TRANSCRIPT_ENTRIES = 4_000;

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
    onActivitySettings: (settings) => {
      transcript.setActivitySettings(settings);
      tui.requestRender();
    },
  });
  let secretOverlay: ReturnType<TUI["showOverlay"]> | null = null;
  let activeSecretInput: MaskedInputComponent | null = null;
  let autocompleteCapabilities = options.session.viewModel.capabilities.join("\u0000");
  let unsubscribe: () => void = () => {};
  let removeInputListener: () => void = () => {};
  let pendingApplicationCommands = 0;
  let secretSubmissionBusy = false;
  let commandTail: Promise<void> = Promise.resolve();

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
      secretSubmissionBusy = true;
      editor.disableSubmit = true;
      void runtime.submitSecret(request, secret).then(
        (lines) => transcript.appendOperatorLines(lines),
        (error) => transcript.appendOperatorLines([`error: ${errorMessage(error)}`]),
      ).finally(() => {
        secretSubmissionBusy = false;
        editor.disableSubmit = false;
        if (!settled) tui.requestRender();
      });
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
    if (!submitted.trim()) return;
    pendingApplicationCommands += 1;
    const executeSubmitted = async () => {
      try {
        const result = await runtime.execute(submitted);
        transcript.appendOperatorLines(result.lines);
        if (result.requestSecret) openSecretPrompt(result.requestSecret);
        if (result.shouldExit) cleanup();
      } catch (error) {
        transcript.appendOperatorLines([`error: ${errorMessage(error)}`]);
      }
    };
    const finishSubmitted = () => {
      pendingApplicationCommands -= 1;
      editor.disableSubmit = secretSubmissionBusy;
      if (!settled) tui.requestRender();
    };
    if (isPriorityTuiSubmission(submitted)) {
      void executeSubmitted().finally(finishSubmitted);
      return;
    }
    commandTail = commandTail.then(executeSubmitted).finally(finishSubmitted);
  };

  unsubscribe = options.session.subscribe((model) => {
    header.setModel(model);
    plan.setModel(model);
    footer.setModel(model);
    transcript.setModel(model);
    editor.disableSubmit = secretSubmissionBusy;
    const nextCapabilities = model.capabilities.join("\u0000");
    if (nextCapabilities !== autocompleteCapabilities) {
      autocompleteCapabilities = nextCapabilities;
      editor.setAutocompleteProvider(createSafeAutocompleteProvider(model.capabilities));
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

export function isPriorityTuiSubmission(value: string): boolean {
  return /^\/stop(?:\s|$)/i.test(value.trim());
}

export function createAutoctxEditor(tui: TUI, capabilities: readonly string[]): Editor {
  const editor = new SafeEditor(tui, AUTOCTX_EDITOR_THEME, {
    paddingX: 1,
    autocompleteMaxVisible: 10,
  });
  editor.setAutocompleteProvider(createSafeAutocompleteProvider(capabilities));
  return editor;
}

function createSafeAutocompleteProvider(capabilities: readonly string[]): AutocompleteProvider {
  return new SafeAutocompleteProvider(new CombinedAutocompleteProvider(
    tuiSlashCommands(capabilities),
    process.cwd(),
  ));
}

/** Never render or insert terminal controls supplied by filesystem entries. */
export class SafeAutocompleteProvider implements AutocompleteProvider {
  readonly #delegate: AutocompleteProvider;

  constructor(delegate: AutocompleteProvider) {
    this.#delegate = delegate;
  }

  async getSuggestions(
    lines: string[],
    cursorLine: number,
    cursorCol: number,
    options: { signal: AbortSignal; force?: boolean },
  ): Promise<AutocompleteSuggestions | null> {
    const suggestions = await this.#delegate.getSuggestions(
      lines,
      cursorLine,
      cursorCol,
      options,
    );
    if (!suggestions) return null;
    const items = suggestions.items.filter(isSafeAutocompleteItem);
    return items.length ? { ...suggestions, items } : null;
  }

  applyCompletion(
    lines: string[],
    cursorLine: number,
    cursorCol: number,
    item: AutocompleteItem,
    prefix: string,
  ): { lines: string[]; cursorLine: number; cursorCol: number } {
    if (!isSafeAutocompleteItem(item)) return { lines, cursorLine, cursorCol };
    return this.#delegate.applyCompletion(lines, cursorLine, cursorCol, item, prefix);
  }

  shouldTriggerFileCompletion(lines: string[], cursorLine: number, cursorCol: number): boolean {
    return this.#delegate.shouldTriggerFileCompletion?.(lines, cursorLine, cursorCol) ?? false;
  }
}

function isSafeAutocompleteItem(item: AutocompleteItem): boolean {
  return [item.value, item.label, item.description ?? ""]
    .every((value) => !/[\u0000-\u001f\u007f-\u009f]/.test(value));
}

/** pi-tui treats C1 bytes as printable paste content; never let them reach render(). */
export class SafeEditor extends Editor {
  override handleInput(data: string): void {
    const bracketedPaste = data.startsWith("\u001b[200~") && data.endsWith("\u001b[201~")
      ? data.slice("\u001b[200~".length, -"\u001b[201~".length)
      : null;
    const candidate = bracketedPaste ?? data;
    // Reject the complete chunk. Stripping only a C1 introducer would turn a
    // sequence such as C1 CSI + "A" into ordinary editor text.
    if (/[\u0080-\u009f]/.test(candidate)) return;
    // Mixed printable/control chunks are paste or protocol data, never a
    // single editor key. Reject them atomically so BEL and other C0 bytes
    // cannot be retained and emitted on a later render.
    if (
      candidate.length > 1 &&
      /[\u0000-\u0008\u000b\u000c\u000e-\u001a\u001c-\u001f]/.test(candidate)
    ) return;
    // Preserve legitimate ESC-prefixed editor keybindings, but never accept a
    // terminal control string as text (including inside bracketed paste).
    if (bracketedPaste !== null && candidate.includes("\u001b")) return;
    const firstEscape = candidate.indexOf("\u001b");
    // A legitimate key sequence is delivered as one ESC-prefixed chunk. Any
    // ESC embedded after printable text (or a second ESC in the same chunk)
    // is untrusted terminal protocol data.
    if (firstEscape > 0 || (firstEscape === 0 && candidate.indexOf("\u001b", 1) >= 0)) return;
    if (/\u001b(?:\]|[P^_X])/.test(candidate)) return;
    super.handleInput(data);
  }
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
      `${this.model.connection.status} · ${displayTuiEndpoint(this.model.endpoint)} · run=${this.model.run.runId ?? "none"} · scenario=${this.model.run.scenario ?? "none"}`,
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
  readonly #buffer = new TuiTranscriptBuffer();

  setModel(model: TuiViewModel): void {
    this.#buffer.setModel(model);
  }

  appendOperatorLines(lines: readonly string[]): void {
    this.#buffer.appendOperatorLines(lines);
  }

  setActivitySettings(settings: TuiActivitySettings): void {
    this.#buffer.setActivitySettings(settings);
  }

  invalidate(): void {}

  render(width: number): string[] {
    return this.#buffer.render(width);
  }
}

type TuiTranscriptEntry =
  | { readonly kind: "semantic"; readonly key: string; readonly row: TuiTranscriptRow }
  | { readonly kind: "operator"; readonly key: string; readonly line: string };

/**
 * Arrival-ordered, bounded presentation state. Durable rows and operator
 * output share one timeline so later server events cannot render above stale
 * command output.
 */
export class TuiTranscriptBuffer {
  readonly #entries: TuiTranscriptEntry[] = [];
  #seenRows = new WeakSet<TuiTranscriptRow>();
  #clientRunId: string | null | undefined;
  #semanticSequence = 0;
  #operatorSequence = 0;
  #activitySettings = DEFAULT_TUI_ACTIVITY_SETTINGS;

  setModel(model: TuiViewModel): void {
    if (this.#clientRunId !== model.run.clientRunId) {
      this.#entries.length = 0;
      this.#seenRows = new WeakSet<TuiTranscriptRow>();
      this.#clientRunId = model.run.clientRunId;
    }
    for (const row of model.transcript) {
      if (this.#seenRows.has(row)) continue;
      this.#seenRows.add(row);
      const key = `semantic:${this.#semanticSequence++}`;
      this.#entries.push({ kind: "semantic", key, row });
    }
    this.#trim();
  }

  appendOperatorLines(lines: readonly string[]): void {
    for (const line of lines) {
      const key = `operator:${this.#operatorSequence++}`;
      this.#entries.push({ kind: "operator", key, line });
    }
    this.#trim();
  }

  setActivitySettings(settings: TuiActivitySettings): void {
    this.#activitySettings = settings;
  }

  render(width: number): string[] {
    const lines = this.#entries.flatMap((entry) => {
      if (entry.kind === "operator") return [entry.line];
      if (!activityRowIsVisible(entry.row, this.#activitySettings)) return [];
      return formatTranscriptRow(entry.row, this.#activitySettings);
    });
    return lines.length
      ? fitLines(lines, width)
      : [truncateToWidth("Waiting for durable transcript events…", Math.max(1, width))];
  }

  #trim(): void {
    if (this.#entries.length > MAX_RENDERED_TRANSCRIPT_ENTRIES) {
      this.#entries.splice(0, this.#entries.length - MAX_RENDERED_TRANSCRIPT_ENTRIES);
    }
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
    const bracketedPaste = data.startsWith("\u001b[200~")
      ? data.replace(/^\u001b\[200~/, "").replace(/\u001b\[201~$/, "")
      : null;
    // Navigation, function, mouse, and terminal-protocol keys are escape
    // sequences. Ignore them atomically so suffixes such as "[A" or "[3~"
    // can never become invisible credential bytes.
    const candidate = bracketedPaste ?? data;
    // C1 controls are alternate encodings for CSI/OSC/DCS and friends. Drop
    // their entire input chunk, just as we do ESC-prefixed terminal keys,
    // rather than stripping only the introducer and accepting its suffix.
    if (/[\u001b\u0080-\u009f]/.test(candidate)) return;
    const printable = candidate.replace(/[\u0000-\u001f\u007f]/g, "");
    if (printable) this.#secret = `${this.#secret}${printable}`.slice(0, 16_384);
  }
}

export function renderTuiTranscriptLines(
  model: TuiViewModel | null,
  operatorLines: readonly string[],
  width: number,
): string[] {
  const buffer = new TuiTranscriptBuffer();
  if (model) buffer.setModel(model);
  buffer.appendOperatorLines(operatorLines);
  return buffer.render(width);
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

function formatTranscriptRow(
  row: TuiTranscriptRow,
  settings: TuiActivitySettings = DEFAULT_TUI_ACTIVITY_SETTINGS,
): string[] {
  const prefix = row.sequence === undefined ? "" : `${row.sequence.toString().padStart(4)} `;
  const activityDetail = row.event === "action_detail" || row.event === "runtime_session_event";
  const showDetail = !activityDetail || settings.verbosity !== "quiet" || row.activity.hasError;
  return [
    `${prefix}${toneIcon(row.tone)} ${row.title}`,
    ...(showDetail && row.detail ? row.detail.split("\n").map((line) => `     ${line}`) : []),
  ];
}

function fitLines(lines: readonly string[], width: number): string[] {
  const safeWidth = Math.max(1, width);
  return lines.flatMap((line) => {
    // All incoming content is plain, untrusted data. Strip terminal controls
    // first; only then introduce locally generated OSC 8 hyperlinks.
    const wrapped = wrapTextWithAnsi(linkifySafeHttpUrls(sanitizeTuiText(line)), safeWidth);
    return (wrapped.length ? wrapped : [""]).map((part) => truncateToWidth(part, safeWidth, ""));
  });
}

export function sanitizeTuiText(value: string): string {
  const withoutControls = stripTerminalControls(value);
  const redacted = redactPresentationText(withoutControls)
    .split(REDACTED_PRESENTATION_VALUE).join("REDACTED");
  return redacted
    .replace(/\b(?:https?|wss?):\/\/[^\s\u001b]+/gi, (url) => redactUrlForTerminal(url));
}

/** Strip terminal protocol controls in one forward pass, consuming unterminated strings to EOF. */
function stripTerminalControls(value: string): string {
  const output: string[] = [];
  let index = 0;
  while (index < value.length) {
    const code = value.charCodeAt(index);
    if (code === 0x1b) {
      const next = value.charCodeAt(index + 1);
      if (next === 0x5d) {
        index = consumeTerminalString(value, index + 2, true);
        continue;
      }
      if (next === 0x50 || next === 0x58 || next === 0x5e || next === 0x5f) {
        index = consumeTerminalString(value, index + 2, false);
        continue;
      }
      if (next === 0x5b) {
        index = consumeCsi(value, index + 2);
        continue;
      }
      index = Math.min(value.length, index + 2);
      continue;
    }
    if (code === 0x9d) {
      index = consumeTerminalString(value, index + 1, true);
      continue;
    }
    if (code === 0x90 || code === 0x98 || code === 0x9e || code === 0x9f) {
      index = consumeTerminalString(value, index + 1, false);
      continue;
    }
    if (code === 0x9b) {
      index = consumeCsi(value, index + 1);
      continue;
    }
    if ((code <= 0x1f && code !== 0x0a) || (code >= 0x7f && code <= 0x9f)) {
      index += 1;
      continue;
    }
    output.push(value[index]!);
    index += 1;
  }
  return output.join("");
}

function consumeTerminalString(value: string, start: number, allowBel: boolean): number {
  let index = start;
  while (index < value.length) {
    const code = value.charCodeAt(index);
    if ((allowBel && code === 0x07) || code === 0x9c) return index + 1;
    if (code === 0x1b && value.charCodeAt(index + 1) === 0x5c) return index + 2;
    index += 1;
  }
  return value.length;
}

function consumeCsi(value: string, start: number): number {
  let index = start;
  while (index < value.length) {
    const code = value.charCodeAt(index);
    index += 1;
    if (code >= 0x40 && code <= 0x7e) return index;
  }
  return value.length;
}

function redactUrlForTerminal(value: string): string {
  const { core, trailing } = splitTrailingUrlPunctuation(value);
  const withoutUserinfo = stripUrlUserinfoLexically(core);
  try {
    const url = new URL(withoutUserinfo);
    url.username = "";
    url.password = "";
    for (const key of [...url.searchParams.keys()]) {
      if (isSensitiveUrlKey(key)) {
        url.searchParams.set(key, "REDACTED");
      }
    }
    if (url.hash.includes("=")) {
      const fragment = new URLSearchParams(url.hash.slice(1));
      for (const key of [...fragment.keys()]) {
        if (isSensitiveUrlKey(key)) fragment.set(key, "REDACTED");
      }
      url.hash = fragment.toString();
    }
    return `${url.toString()}${trailing}`;
  } catch {
    return `${redactMalformedUrlQueryValues(withoutUserinfo)}${trailing}`;
  }
}

function linkifySafeHttpUrls(value: string): string {
  return value.replace(/\bhttps?:\/\/[^\s\u001b]+/gi, (value) => {
    const { core, trailing } = splitTrailingUrlPunctuation(value);
    return `${hyperlink(core, core)}${trailing}`;
  });
}

function splitTrailingUrlPunctuation(value: string): { core: string; trailing: string } {
  const match = value.match(/[\]\[(){}<>.,;!?"']+$/);
  if (!match) return { core: value, trailing: "" };
  return {
    core: value.slice(0, -match[0].length),
    trailing: match[0],
  };
}

function stripUrlUserinfoLexically(value: string): string {
  const schemeEnd = value.indexOf("://");
  if (schemeEnd < 0) return value;
  const authorityStart = schemeEnd + 3;
  const pathStartCandidates = [value.indexOf("/", authorityStart), value.indexOf("?", authorityStart), value.indexOf("#", authorityStart)]
    .filter((index) => index >= 0);
  const authorityEnd = pathStartCandidates.length ? Math.min(...pathStartCandidates) : value.length;
  const authority = value.slice(authorityStart, authorityEnd);
  const userinfoEnd = authority.lastIndexOf("@");
  return userinfoEnd < 0
    ? value
    : `${value.slice(0, authorityStart)}${authority.slice(userinfoEnd + 1)}${value.slice(authorityEnd)}`;
}

function redactMalformedUrlQueryValues(value: string): string {
  // If URL parsing failed, parameter names cannot be normalized reliably
  // (including percent-encoded spellings). Fail closed and redact every value.
  return value.replace(/([?&#])([^=&#]+)=([^&#]*)/g, (_match, separator: string, key: string) =>
    `${separator}${key}=REDACTED`);
}

function isSensitiveUrlKey(key: string): boolean {
  const normalized = key.toLowerCase().replace(/[^a-z0-9]/g, "");
  return /^(?:api)?key$|auth$|bearer$|token$|authorization$|credential$|jwt$|password$|secret$|signature$|sig$/.test(normalized);
}

function activityRowIsVisible(row: TuiTranscriptRow, settings: TuiActivitySettings): boolean {
  const activity = row.activity;
  if (!activity || (row.event !== "action_detail" && row.event !== "runtime_session_event")) {
    return true;
  }
  switch (settings.filter) {
    case "all":
      return true;
    case "runtime":
      return activity.family === "runtime";
    case "prompts":
      return activity.focus === "prompt";
    case "commands":
      return activity.focus === "command";
    case "children":
      return activity.focus === "child";
    case "errors":
      return activity.hasError;
  }
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
