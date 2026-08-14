import { formatTuiChatResponseLines } from "./chat-command.js";
import { renderRunStatusPresentation } from "../domain/run-status-presentation.js";
import {
  assertTuiCommandAvailable,
  formatTuiCommandHelp,
  resolveTuiCommand,
} from "./command-registry.js";
import {
  formatTuiReadFailure,
  type TuiReadModelClient,
  type TuiReadResult,
  type TuiRunStatusReadModel,
} from "./read-model-client.js";
import type { TuiSession } from "./session.js";
import { hyperlink } from "./pi-tui-adapter.js";

export interface TuiSecretRequest {
  readonly provider: string;
  readonly model?: string;
  readonly baseUrl?: string;
}

export interface TuiRegisteredCommandResult {
  readonly lines: readonly string[];
  readonly shouldExit?: boolean;
  readonly requestSecret?: TuiSecretRequest;
}

export interface TuiCommandRuntimeOptions {
  readonly session: TuiSession;
  readonly readModels: TuiReadModelClient;
  readonly onAsyncLines?: (lines: readonly string[]) => void;
}

export class TuiCommandRuntime {
  readonly session: TuiSession;
  readonly readModels: TuiReadModelClient;
  readonly #onAsyncLines: (lines: readonly string[]) => void;
  #pendingStopRunId: string | null = null;
  #watchController: AbortController | null = null;

  constructor(options: TuiCommandRuntimeOptions) {
    this.session = options.session;
    this.readModels = options.readModels;
    this.#onAsyncLines = options.onAsyncLines ?? (() => undefined);
  }

  detach(): void {
    this.#watchController?.abort("TUI detached");
    this.#watchController = null;
  }

  async submitSecret(request: TuiSecretRequest, secret: string): Promise<readonly string[]> {
    if (!secret) throw new Error("credential cannot be empty");
    await this.session.login(request.provider, secret, request.model, request.baseUrl);
    return [`authenticated ${request.provider}`];
  }

  async execute(raw: string): Promise<TuiRegisteredCommandResult> {
    const value = raw.trim();
    if (!value) return { lines: [] };
    const resolved = resolveTuiCommand(value);
    if (!resolved) return { lines: ["unknown command; use /help"] };
    const { descriptor, args } = resolved;
    assertTuiCommandAvailable(descriptor, this.session.viewModel.capabilities);
    if (descriptor.name !== "stop") this.#pendingStopRunId = null;

    const executor = descriptor.executor;
    switch (executor) {
      case "help":
        return { lines: formatTuiCommandHelp(this.session.viewModel.capabilities) };
      case "quit":
        this.detach();
        return { lines: [], shouldExit: true };
      case "stop":
        return this.#stop(args);
      case "run":
        return this.#run(args);
      case "pause":
        await this.session.pause();
        return { lines: ["pause acknowledged"] };
      case "resume":
        await this.session.resume();
        return { lines: ["resume acknowledged"] };
      case "hint":
        if (!args) return { lines: ["usage: /hint <text>"] };
        await this.session.injectHint(args);
        return { lines: ["operator hint acknowledged"] };
      case "gate":
        if (!isGateDecision(args)) {
          return { lines: ["usage: /gate <advance|retry|rollback>"] };
        }
        await this.session.overrideGate(args);
        return { lines: [`gate override acknowledged: ${args}`] };
      case "status":
        return { lines: await this.#statusLines(targetRunId(args, this.session)) };
      case "show":
        return { lines: await this.#showLines(args) };
      case "artifacts":
        return { lines: await this.#artifactLines(targetRunId(args, this.session), false) };
      case "export":
        return { lines: await this.#artifactLines(targetRunId(args, this.session), true) };
      case "watch":
        return this.#startWatch(targetRunId(args, this.session));
      case "timeline":
        return { lines: resultLines(await this.readModels.runTimeline(targetRunId(args, this.session))) };
      case "findings":
        return { lines: resultLines(await this.readModels.runFindings(targetRunId(args, this.session))) };
      case "approve":
        return { lines: await this.#playbookDecision(args, "approve") };
      case "reject":
        return { lines: await this.#playbookDecision(args, "reject") };
      case "runs":
        return { lines: formatRuns(await this.readModels.listRuns()) };
      case "queue":
        return { lines: formatQueue(await this.readModels.queueState(), false) };
      case "workers":
        return { lines: formatQueue(await this.readModels.queueState(), true) };
      case "sessions":
        return { lines: await this.#sessionListLines() };
      case "session":
        return { lines: await this.#sessionDetailLines(args) };
      case "chat":
        return this.#chat(args);
      case "solve":
        return this.#solve(args);
      case "scenarios":
        return { lines: formatScenarios(this.session) };
      case "routing":
        return { lines: formatRouting(this.session) };
      case "login":
        return this.#login(args);
      case "logout":
        await this.session.logout(args || undefined);
        return { lines: [`logged out${args ? ` of ${args}` : ""}`] };
      case "provider":
        if (!args) return { lines: ["usage: /provider <name>"] };
        await this.session.switchProvider(args);
        return { lines: [`active provider: ${args}`] };
      case "whoami": {
        const status = await this.session.whoami();
        return {
          lines: [
            `provider: ${status.provider}`,
            `authenticated: ${status.authenticated ? "yes" : "no"}`,
            ...(status.model ? [`model: ${status.model}`] : []),
          ],
        };
      }
      case "activity":
        return { lines: ["Activity filters apply to detail rows only; lifecycle and operator events always remain visible."] };
      default:
        return assertNever(executor);
    }
  }

  async #stop(args: string): Promise<TuiRegisteredCommandResult> {
    const runId = this.session.viewModel.run.runId;
    if (!runId || !this.session.viewModel.run.active) {
      return { lines: ["no active run is attached"] };
    }
    if (args !== "confirm" || this.#pendingStopRunId !== runId) {
      this.#pendingStopRunId = runId;
      return {
        lines: [
          `Stop run ${runId}? This affects the run, while /quit only detaches.`,
          "Type /stop confirm to continue.",
        ],
      };
    }
    this.#pendingStopRunId = null;
    const decision = await this.session.stopActiveRun();
    return { lines: [`stop ${decision} for ${runId}`] };
  }

  async #run(args: string): Promise<TuiRegisteredCommandResult> {
    const [scenario, iterationsRaw = "5", ...extra] = args.split(/\s+/);
    if (!scenario || extra.length) return { lines: ["usage: /run <scenario> [positive-iterations]"] };
    const iterations = Number(iterationsRaw);
    if (!Number.isInteger(iterations) || iterations <= 0) {
      return { lines: ["usage: /run <scenario> [positive-iterations]"] };
    }
    const scenarioInfo = this.session.viewModel.scenarios.find((item) => item.name === scenario);
    if (scenarioInfo && !scenarioInfo.available) {
      return { lines: [`scenario '${scenario}' is known but unavailable from this server`] };
    }
    const runId = await this.session.startRun(scenario, iterations);
    return { lines: [`accepted run ${runId}`] };
  }

  async #showLines(args: string): Promise<readonly string[]> {
    const tokens = args.split(/\s+/).filter(Boolean);
    const best = tokens.includes("--best");
    const explicit = tokens.find((token) => !token.startsWith("--"));
    const inspection = await this.readModels.runInspection(targetRunId(explicit ?? "", this.session));
    if (!inspection.ok) return [formatTuiReadFailure(inspection)];
    const selected = best ? inspection.value.best_generation : inspection.value.latest_generation;
    const outputs = best ? inspection.value.best_outputs : inspection.value.latest_outputs;
    return [
      `Run ${String(inspection.value.run.run_id ?? "unknown")}`,
      `  status: ${String(inspection.value.run.status ?? "unknown")}`,
      `  scenario: ${String(inspection.value.run.scenario ?? "unknown")}`,
      selected ? `selected generation: ${String(selected.generation ?? "unknown")}` : "selected generation: unavailable",
      selected ? `best score: ${String(selected.best_score ?? "unknown")}` : "best score: unavailable",
      ...(recordValue(inspection.value.runtime_session)
        ? [`runtime session: ${String(recordValue(inspection.value.runtime_session)?.session_id ?? "unknown")}`]
        : []),
      ...outputs.flatMap((output) => [
        `[${output.role}]`,
        ...output.content.split("\n"),
      ]),
      "Artifacts:",
      ...Object.entries(inspection.value.artifact_discovery)
        .map(([name, path]) => `  ${this.#artifactLink(name, path)}`),
    ];
  }

  async #artifactLines(runId: string, exportOnly: boolean): Promise<readonly string[]> {
    const result = await this.readModels.runInspection(runId);
    if (!result.ok) return [formatTuiReadFailure(result)];
    const entries = Object.entries(result.value.artifact_discovery)
      .filter(([name]) => !exportOnly || name === "export");
    return entries.length
      ? entries.map(([name, path]) => this.#artifactLink(name, path))
      : [exportOnly ? "export discovery is unsupported" : "no artifacts discovered"];
  }

  #artifactLink(name: string, path: string): string {
    const url = new URL(path, `${this.readModels.baseUrl}/`).toString();
    return `${name}: ${hyperlink(url, url)}`;
  }

  async #sessionListLines(): Promise<readonly string[]> {
    const [background, runtime] = await Promise.all([
      this.readModels.listBackgroundSessions(),
      this.readModels.listRuntimeSessions(),
    ]);
    const backgroundLines = background.ok
      ? background.value.sessions.map((session) =>
          `  ${session.session_id} · ${session.status} · run=${session.run_id || "none"} · parent=${session.parent_session_id || "none"} · children=${session.child_session_count}`)
      : [`  ${formatTuiReadFailure(background)}`];
    const runtimeLines = runtime.ok
      ? runtime.value.sessions.map((session) =>
          `  ${session.session_id} · events=${session.event_count} · task=${session.task_id || "none"} · parent=${session.parent_session_id || "none"}`)
      : [`  ${formatTuiReadFailure(runtime)}`];
    return [
      "Background sessions:",
      ...(backgroundLines.length ? backgroundLines : ["  (none)"]),
      "Runtime sessions:",
      ...(runtimeLines.length ? runtimeLines : ["  (none)"]),
      "Use /session <session-id> to inspect parent and child relationships.",
    ];
  }

  async #sessionDetailLines(sessionId: string): Promise<readonly string[]> {
    const cleanSessionId = sessionId.trim();
    if (!cleanSessionId) return ["usage: /session <session-id>"];
    const background = await this.readModels.backgroundSession(cleanSessionId);
    if (background.ok) return prettySemanticLines(background.value);
    if (background.kind !== "not_found") return [formatTuiReadFailure(background)];
    const runtime = await this.readModels.runtimeSession(cleanSessionId);
    return resultLines(runtime);
  }

  #startWatch(runId: string): TuiRegisteredCommandResult {
    this.#watchController?.abort("watch replaced");
    const controller = new AbortController();
    this.#watchController = controller;
    void this.readModels.watchRun(runId, {
      signal: controller.signal,
      onUpdate: (status) => this.#onAsyncLines([`watch update · ${runId}`, ...formatTuiRunStatus(status)]),
    }).then((result) => {
      if (this.#watchController === controller) this.#watchController = null;
      if (!result.ok && result.detail !== "watch detached") {
        this.#onAsyncLines([formatTuiReadFailure(result)]);
      } else if (result.ok) {
        this.#onAsyncLines([`watch ended · ${runId} · ${result.value.status}`]);
      }
    });
    return { lines: [`watching ${runId}; /quit detaches without stopping it`] };
  }

  async #chat(args: string): Promise<TuiRegisteredCommandResult> {
    const match = args.match(/^(\S+)\s+([\s\S]+)$/);
    if (!match) return { lines: ["usage: /chat <role> <message>"] };
    const [, role, message] = match;
    const response = await this.session.chat(role!, message!);
    return { lines: formatTuiChatResponseLines(role!, response) };
  }

  async #playbookDecision(
    args: string,
    action: "approve" | "reject",
  ): Promise<readonly string[]> {
    const [scenario, confirmation, ...extra] = args.split(/\s+/).filter(Boolean);
    if (!scenario || confirmation !== "confirm" || extra.length) {
      return [`usage: /${action} <scenario> confirm`];
    }
    const result = action === "approve"
      ? await this.readModels.approvePlaybook(scenario)
      : await this.readModels.rejectPlaybook(scenario);
    if (!result.ok) return [formatTuiReadFailure(result)];
    this.session.resolvePendingDecision(scenario);
    return [`${action === "approve" ? "approved" : "rejected"} pending playbook for ${scenario}`];
  }

  async #solve(goal: string): Promise<TuiRegisteredCommandResult> {
    if (!goal) return { lines: ["usage: /solve <plain-language goal>"] };
    const preview = await this.session.createScenario(goal.replace(/^"|"$/g, "").trim());
    const scenario = await this.session.confirmScenario();
    const runId = await this.session.startRun(scenario.name || preview.name, 5);
    return { lines: [`created scenario ${scenario.name}`, `accepted run ${runId}`] };
  }

  #login(args: string): TuiRegisteredCommandResult {
    const [provider, model, baseUrl, ...extra] = args.split(/\s+/).filter(Boolean);
    if (!provider || extra.length) {
      return { lines: ["usage: /login <provider> [model] [baseUrl]"] };
    }
    return {
      lines: [`enter API key for ${provider}; input is masked and excluded from history`],
      requestSecret: {
        provider,
        ...(model ? { model } : {}),
        ...(baseUrl ? { baseUrl } : {}),
      },
    };
  }

  async #statusLines(runId: string): Promise<readonly string[]> {
    const result = await this.readModels.runStatus(runId);
    return result.ok ? formatTuiRunStatus(result.value) : [formatTuiReadFailure(result)];
  }
}

function targetRunId(explicit: string, session: TuiSession): string {
  const runId = explicit.trim() || session.viewModel.run.runId;
  if (!runId) throw new Error("run id is required and no active run is attached");
  return runId;
}

export function formatTuiRunStatus(status: TuiRunStatusReadModel): string[] {
  const latest = status.generations.reduce<(typeof status.generations)[number] | undefined>(
    (selected, generation) =>
      !selected || generation.generation > selected.generation ? generation : selected,
    undefined,
  );
  const runtimeSessionId = status.runtime_session?.session_id;
  const progress = status.progress_report;
  const latestPassAtK = progress?.pass_at_k.at(-1);
  return renderRunStatusPresentation({
    runId: status.run_id,
    status: status.status,
    scenario: status.scenario_name,
    completedGenerations: status.generations.length,
    targetGenerations: status.target_generations,
    ...(latest
      ? {
          latestGeneration: {
            generation: latest.generation,
            bestScore: latest.best_score,
            gateDecision: latest.gate_decision,
          },
        }
      : {}),
    ...(progress
      ? {
          progress: {
            bestScore: progress.best_score,
            threshold: progress.threshold,
            ...(latestPassAtK
              ? { latestPassAtK: { k: latestPassAtK.k, passed: latestPassAtK.passed } }
              : {}),
          },
        }
      : {}),
    ...(runtimeSessionId ? { runtimeSessionId } : {}),
  });
}

function resultLines(result: TuiReadResult<unknown>): string[] {
  if (!result.ok) return [formatTuiReadFailure(result)];
  return prettySemanticLines(result.value);
}

function prettySemanticLines(value: unknown, indent = ""): string[] {
  if (Array.isArray(value)) {
    if (!value.length) return [`${indent}(none)`];
    return value.flatMap((item) => prettySemanticLines(item, indent));
  }
  if (!value || typeof value !== "object") return [`${indent}${String(value)}`];
  return Object.entries(recordValue(value) ?? {}).flatMap(([key, item]) => {
    if (Array.isArray(item) || (item && typeof item === "object")) {
      return [`${indent}${key}:`, ...prettySemanticLines(item, `${indent}  `)];
    }
    return [`${indent}${key}: ${String(item ?? "unknown")}`];
  });
}

function recordValue(value: unknown): Record<string, unknown> | null {
  return isRecord(value) ? value : null;
}

function isRecord(value: unknown): value is Record<string, unknown> {
  return value !== null && typeof value === "object" && !Array.isArray(value);
}

function formatRuns(result: Awaited<ReturnType<TuiReadModelClient["listRuns"]>>): string[] {
  if (!result.ok) return [formatTuiReadFailure(result)];
  if (!result.value.length) return ["No active or recent runs."];
  return [
    "Recent runs:",
    ...result.value.map((run) =>
      `  ${run.run_id} · ${run.status} · ${run.scenario_name} · ${run.generations_completed} generations`),
  ];
}

function formatQueue(
  result: Awaited<ReturnType<TuiReadModelClient["queueState"]>>,
  workersOnly: boolean,
): string[] {
  if (!result.ok) return [formatTuiReadFailure(result)];
  if (workersOnly) {
    return result.value.workers.length
      ? ["Workers:", ...result.value.workers.map((worker) =>
        `  ${worker.worker_id} · ${worker.state} · task=${worker.current_task_id ?? "none"} · ${worker.progress_digest}`)]
      : ["Worker state is unknown; no runtime-session heartbeats are available."];
  }
  return result.value.tasks.length
    ? ["Task queue:", ...result.value.tasks.map((task) =>
      `  ${task.id} · ${task.state} · ${task.spec_name} · attempts=${task.attempts}${task.error ? ` · ${task.error}` : ""}`)]
    : ["Task queue is empty."];
}

function formatScenarios(session: TuiSession): string[] {
  const scenarios = session.viewModel.scenarios;
  return scenarios.length
    ? ["Scenarios:", ...scenarios.map((scenario) =>
      `  ${scenario.name} · ${scenario.origin} · ${scenario.available ? "available" : "unavailable"} · ${scenario.description}`)]
    : ["No scenarios advertised by the server."];
}

function formatRouting(session: TuiSession): string[] {
  const routing = session.viewModel.routing;
  return [
    `provider: ${routing.provider}`,
    `model: ${routing.model ?? "unknown"}`,
    `hosting: ${routing.hostingClass ?? "unknown"}`,
    `capability tier: ${routing.capabilityTier ?? "unknown"}`,
    ...Object.entries(routing.roles).map(([role, route]) =>
      `${role}: ${route.provider}/${route.model}${route.capabilityTier ? ` · ${route.capabilityTier}` : ""}`),
  ];
}

function isGateDecision(value: string): value is "advance" | "retry" | "rollback" {
  return value === "advance" || value === "retry" || value === "rollback";
}

function assertNever(value: never): never {
  throw new Error(`Unhandled TUI command executor: ${String(value)}`);
}
