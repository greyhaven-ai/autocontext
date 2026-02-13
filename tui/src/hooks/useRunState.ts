import { useReducer } from "react";
import type {
  RunState,
  Role,
  RoleState,
  GateDecision,
  CuratorDecision,
  ServerMessage,
  TrajectoryRow,
  ScenarioInfo,
  ExecutorInfo,
  ScenarioCreationState,
} from "../types.js";

/** Parse /run, /scenarios, and /scenario create commands from chat input. Returns null if not a command. */
export function parseCommand(input: string): { type: "start_run"; scenario: string; generations: number } | { type: "list_scenarios" } | { type: "create_scenario"; description: string } | { type: "unknown"; text: string } | null {
  const trimmed = input.trim();
  if (!trimmed.startsWith("/")) return null;

  const parts = trimmed.split(/\s+/);
  const cmd = parts[0]!.toLowerCase();

  if (cmd === "/run" || cmd === "/start") {
    const scenario = parts[1] ?? "grid_ctf";
    const generations = parseInt(parts[2] ?? "5", 10);
    return { type: "start_run", scenario, generations: isNaN(generations) ? 5 : generations };
  }

  if (cmd === "/scenarios" || cmd === "/list") {
    return { type: "list_scenarios" };
  }

  if (cmd === "/scenario" && parts[1]?.toLowerCase() === "create") {
    const description = parts.slice(2).join(" ");
    if (description) {
      return { type: "create_scenario", description };
    }
    return { type: "unknown", text: trimmed };
  }

  return { type: "unknown", text: trimmed };
}

const ROLES: Role[] = ["competitor", "analyst", "coach", "architect", "curator"];
const CHAT_TARGETS: Role[] = ["analyst", "competitor", "coach", "architect", "curator"];

function makeDefaultRoleState(): RoleState {
  return { status: "waiting", latencyMs: null, tokens: null };
}

function makeDefaultScenarioCreation(): ScenarioCreationState {
  return { phase: "idle", name: null, preview: null, errorMessage: null, testScores: null };
}

function makeDefaultRoles(): Record<Role, RoleState> {
  return {
    competitor: makeDefaultRoleState(),
    analyst: makeDefaultRoleState(),
    coach: makeDefaultRoleState(),
    architect: makeDefaultRoleState(),
    curator: makeDefaultRoleState(),
  };
}

export function initialState(): RunState {
  return {
    connected: false,
    runId: null,
    scenario: null,
    paused: false,
    totalGenerations: null,
    currentGeneration: null,
    generationStartedAt: null,
    phase: null,
    roles: makeDefaultRoles(),
    trajectory: [],
    tournament: {
      totalMatches: 0,
      completedMatches: 0,
      scores: [],
      meanScore: null,
      bestScore: null,
      wins: null,
      losses: null,
    },
    gateDecision: null,
    gateDelta: null,
    curatorDecision: null,
    chatMessages: [],
    chatTarget: "analyst",
    logLines: [],
    scenarioCreation: makeDefaultScenarioCreation(),
    scenarios: [],
    executors: [],
    currentExecutor: null,
    agentProvider: null,
  };
}

// --- Actions ---

export type Action =
  | { type: "SET_CONNECTED"; connected: boolean }
  | { type: "SERVER_MESSAGE"; message: ServerMessage }
  | { type: "CYCLE_CHAT_TARGET" }
  | { type: "ADD_USER_CHAT"; text: string; target: Role }
  | { type: "ADD_LOG"; line: string }
  | { type: "RESET_SCENARIO_CREATION" };

const MAX_LOG_LINES = 50;
const MAX_CHAT_MESSAGES = 100;

function addLog(state: RunState, line: string): RunState {
  const logLines = [...state.logLines, line].slice(-MAX_LOG_LINES);
  return { ...state, logLines };
}

function handleEvent(state: RunState, event: string, payload: Record<string, unknown>): RunState {
  switch (event) {
    case "run_started": {
      const runId = payload.run_id as string;
      const scenario = (payload.scenario as string) ?? null;
      return addLog(
        {
          ...state,
          runId,
          scenario,
          currentGeneration: null,
          trajectory: [],
          roles: makeDefaultRoles(),
          gateDecision: null,
          gateDelta: null,
          curatorDecision: null,
        },
        `Run started: ${runId} (${scenario})`,
      );
    }

    case "generation_started": {
      const gen = payload.generation as number;
      return addLog(
        {
          ...state,
          currentGeneration: gen,
          generationStartedAt: Date.now(),
          phase: "agents",
          roles: makeDefaultRoles(),
          gateDecision: null,
          gateDelta: null,
          curatorDecision: null,
          tournament: {
            totalMatches: 0,
            completedMatches: 0,
            scores: [],
            meanScore: null,
            bestScore: null,
            wins: null,
            losses: null,
          },
        },
        `Generation ${gen} started`,
      );
    }

    case "agents_started": {
      const activeRoles = (payload.roles as string[]) ?? [];
      const roles = { ...state.roles };
      for (const r of ROLES) {
        if (activeRoles.includes(r)) {
          roles[r] = { ...roles[r], status: "running" };
        } else {
          roles[r] = { ...roles[r], status: "n/a" };
        }
      }
      return addLog({ ...state, roles, phase: "agents" }, `Agents started: ${activeRoles.join(", ")}`);
    }

    case "role_completed": {
      const role = payload.role as Role;
      const latencyMs = (payload.latency_ms as number) ?? null;
      const tokens = (payload.tokens as number) ?? null;
      const roles = {
        ...state.roles,
        [role]: { status: "done" as const, latencyMs, tokens },
      };
      return addLog(
        { ...state, roles },
        `${role} completed (${latencyMs ? (latencyMs / 1000).toFixed(1) + "s" : "?"})`,
      );
    }

    case "tournament_started": {
      const matches = payload.matches as number;
      return addLog(
        {
          ...state,
          phase: "tournament",
          tournament: {
            totalMatches: matches,
            completedMatches: 0,
            scores: [],
            meanScore: null,
            bestScore: null,
            wins: null,
            losses: null,
          },
        },
        `Tournament started (${matches} matches)`,
      );
    }

    case "match_completed": {
      const score = payload.score as number;
      const matchIndex = payload.match_index as number;
      const tournament = {
        ...state.tournament,
        completedMatches: matchIndex + 1,
        scores: [...state.tournament.scores, score],
      };
      return addLog({ ...state, tournament }, `Match ${matchIndex + 1}: score ${score.toFixed(3)}`);
    }

    case "tournament_completed": {
      const tournament = {
        ...state.tournament,
        completedMatches: state.tournament.totalMatches,
        meanScore: (payload.mean_score as number) ?? null,
        bestScore: (payload.best_score as number) ?? null,
        wins: (payload.wins as number) ?? null,
        losses: (payload.losses as number) ?? null,
      };
      return addLog(
        { ...state, tournament, phase: "gate" },
        `Tournament done: mean=${tournament.meanScore?.toFixed(3)} best=${tournament.bestScore?.toFixed(3)}`,
      );
    }

    case "gate_decided": {
      const decision = payload.decision as GateDecision;
      const delta = (payload.delta as number) ?? null;
      return addLog(
        { ...state, gateDecision: decision, gateDelta: delta },
        `Gate: ${decision.toUpperCase()} (delta=${delta?.toFixed(3) ?? "?"})`,
      );
    }

    case "curator_started": {
      const roles = {
        ...state.roles,
        curator: { status: "running" as const, latencyMs: null, tokens: null },
      };
      return addLog({ ...state, roles, phase: "curator" }, "Curator started");
    }

    case "curator_completed": {
      const curatorDecision = (payload.decision as CuratorDecision) ?? null;
      const roles = {
        ...state.roles,
        curator: { ...state.roles.curator, status: "done" as const },
      };
      return addLog(
        { ...state, roles, curatorDecision },
        `Curator: ${curatorDecision ?? "done"}`,
      );
    }

    case "generation_completed": {
      const gen = payload.generation as number;
      const meanScore = payload.mean_score as number;
      const bestScore = payload.best_score as number;
      const elo = payload.elo as number;
      const gate = (payload.gate_decision as GateDecision) ?? state.gateDecision ?? "advance";
      const row: TrajectoryRow = {
        generation: gen,
        meanScore,
        bestScore,
        elo,
        gate,
      };
      const trajectory = [...state.trajectory, row];
      return addLog(
        { ...state, trajectory, phase: null },
        `Generation ${gen} completed: elo=${elo.toFixed(0)} gate=${gate}`,
      );
    }

    case "run_completed": {
      const completedGens = payload.completed_generations as number;
      return addLog(
        { ...state, phase: null, totalGenerations: completedGens },
        `Run completed (${completedGens} generations)`,
      );
    }

    default:
      return addLog(state, `Event: ${event}`);
  }
}

function reducer(state: RunState, action: Action): RunState {
  switch (action.type) {
    case "SET_CONNECTED":
      return { ...state, connected: action.connected };

    case "SERVER_MESSAGE": {
      const msg = action.message;
      if (msg.type === "event") {
        return handleEvent(state, msg.event, msg.payload);
      }
      if (msg.type === "state") {
        return {
          ...state,
          paused: msg.paused,
          currentGeneration: msg.generation,
          phase: msg.phase,
        };
      }
      if (msg.type === "chat_response") {
        const chatMessages = [
          ...state.chatMessages,
          { sender: msg.role, text: msg.text },
        ].slice(-MAX_CHAT_MESSAGES);
        return addLog({ ...state, chatMessages }, `[${msg.role}] ${msg.text.slice(0, 60)}`);
      }
      if (msg.type === "environments") {
        const scenarioNames = msg.scenarios.map((s: ScenarioInfo) => s.name);
        const availableExecutors = msg.executors
          .filter((e: ExecutorInfo) => e.available)
          .map((e: ExecutorInfo) => e.mode);
        return addLog(
          {
            ...state,
            scenarios: msg.scenarios,
            executors: msg.executors,
            currentExecutor: msg.current_executor,
            agentProvider: msg.agent_provider,
          },
          `Environments: ${scenarioNames.join(", ")} | executors: ${availableExecutors.join(", ")}`,
        );
      }
      if (msg.type === "run_accepted") {
        return addLog(
          { ...state, runId: msg.run_id, scenario: msg.scenario, totalGenerations: msg.generations },
          `Run accepted: ${msg.run_id} (${msg.scenario}, ${msg.generations} gens)`,
        );
      }
      if (msg.type === "error") {
        const chatMessages = [
          ...state.chatMessages,
          { sender: "system", text: msg.message },
        ].slice(-MAX_CHAT_MESSAGES);
        return addLog({ ...state, chatMessages }, `Error: ${msg.message}`);
      }
      if (msg.type === "ack") {
        return addLog(state, `Ack: ${msg.action}${msg.decision ? ` (${msg.decision})` : ""}`);
      }
      if (msg.type === "scenario_generating") {
        return addLog(
          {
            ...state,
            scenarioCreation: {
              ...makeDefaultScenarioCreation(),
              phase: "generating",
              name: msg.name,
            },
          },
          `Generating scenario: ${msg.name}...`,
        );
      }
      if (msg.type === "scenario_preview") {
        const preview = {
          name: msg.name,
          displayName: msg.display_name,
          description: msg.description,
          strategyParams: msg.strategy_params,
          scoringComponents: msg.scoring_components,
          constraints: msg.constraints,
          winThreshold: msg.win_threshold,
        };
        return addLog(
          {
            ...state,
            scenarioCreation: {
              ...state.scenarioCreation,
              phase: "preview",
              preview,
              name: msg.name,
            },
          },
          `Scenario preview ready: ${msg.display_name}`,
        );
      }
      if (msg.type === "scenario_ready") {
        return addLog(
          {
            ...state,
            scenarioCreation: {
              ...state.scenarioCreation,
              phase: "ready",
              name: msg.name,
              testScores: msg.test_scores,
            },
          },
          `Scenario ready: ${msg.name} (test scores: ${msg.test_scores.map((s) => s.toFixed(3)).join(", ")})`,
        );
      }
      if (msg.type === "scenario_error") {
        return addLog(
          {
            ...state,
            scenarioCreation: {
              ...state.scenarioCreation,
              phase: "error",
              errorMessage: msg.message,
            },
          },
          `Scenario error: ${msg.message}`,
        );
      }
      return state;
    }

    case "CYCLE_CHAT_TARGET": {
      const idx = CHAT_TARGETS.indexOf(state.chatTarget);
      const next = CHAT_TARGETS[(idx + 1) % CHAT_TARGETS.length]!;
      return { ...state, chatTarget: next };
    }

    case "ADD_USER_CHAT": {
      const chatMessages = [
        ...state.chatMessages,
        { sender: "you", text: action.text },
      ].slice(-MAX_CHAT_MESSAGES);
      return { ...state, chatMessages };
    }

    case "ADD_LOG":
      return addLog(state, action.line);

    case "RESET_SCENARIO_CREATION":
      return { ...state, scenarioCreation: makeDefaultScenarioCreation() };

    default:
      return state;
  }
}

export function useRunState() {
  const [state, dispatch] = useReducer(reducer, undefined, initialState);
  return { state, dispatch };
}
