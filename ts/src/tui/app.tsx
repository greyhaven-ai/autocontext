import React, { useEffect, useMemo, useState } from "react";
import { Box, Text, useApp, useInput } from "ink";
import TextInput from "ink-text-input";
import type { RunManager, RunManagerState } from "../server/run-manager.js";
import type { EventCallback } from "../loop/events.js";

interface InteractiveTuiProps {
  manager: RunManager;
  serverUrl: string;
}

const MAX_LOG_LINES = 18;

function summarizeEvent(event: string, payload: Record<string, unknown>): string | null {
  switch (event) {
    case "run_started":
      return `run ${payload.run_id as string} started for ${payload.scenario as string}`;
    case "generation_started":
      return `generation ${String(payload.generation)} started`;
    case "role_completed":
      return `${String(payload.role)} finished in ${String(payload.latency_ms)}ms`;
    case "tournament_completed":
      return `tournament mean=${Number(payload.mean_score ?? 0).toFixed(3)} best=${Number(payload.best_score ?? 0).toFixed(3)}`;
    case "gate_decided":
      return `gate ${String(payload.decision)} (delta=${String(payload.delta ?? "?")})`;
    case "generation_completed":
      return `generation ${String(payload.generation)} stored`;
    case "run_completed":
      return `run completed after ${String(payload.completed_generations)} generations`;
    case "run_failed":
      return `run failed: ${String(payload.error ?? "unknown error")}`;
    default:
      return null;
  }
}

function formatCommandHelp(): string[] {
  return [
    "/run <scenario> [gens]",
    "/pause or /resume",
    "/hint <text>",
    "/gate <advance|retry|rollback>",
    "/chat <role> <message>",
    "/scenarios",
    "/quit",
  ];
}

export function InteractiveTui({ manager, serverUrl }: InteractiveTuiProps) {
  const { exit } = useApp();
  const [state, setState] = useState<RunManagerState>(manager.getState());
  const [input, setInput] = useState("");
  const [logs, setLogs] = useState<string[]>([
    `interactive server: ${serverUrl}`,
    `available scenarios: ${manager.listScenarios().join(", ")}`,
    ...formatCommandHelp(),
  ]);

  useEffect(() => {
    const handleState = (next: RunManagerState) => {
      setState(next);
    };
    const handleEvent: EventCallback = (event, payload) => {
      const line = summarizeEvent(event, payload);
      if (line) {
        setLogs((prev) => [...prev, line].slice(-MAX_LOG_LINES));
      }
    };

    manager.subscribeState(handleState);
    manager.subscribeEvents(handleEvent);
    return () => {
      manager.unsubscribeState(handleState);
      manager.unsubscribeEvents(handleEvent);
    };
  }, [manager]);

  useInput((value, key) => {
    if (value === "c" && key.ctrl) {
      exit();
    }
  });

  const statusText = useMemo(() => {
    if (!state.active) {
      return state.paused ? "idle (paused)" : "idle";
    }
    const generation = state.generation ? `gen ${state.generation}` : "waiting";
    const phase = state.phase ?? "running";
    return `${generation} • ${phase}${state.paused ? " • paused" : ""}`;
  }, [state]);

  const submit = async (raw: string) => {
    const value = raw.trim();
    setInput("");
    if (!value) {
      return;
    }

    if (value === "/quit" || value === "/exit") {
      exit();
      return;
    }

    if (value === "/help") {
      setLogs((prev) => [...prev, ...formatCommandHelp()].slice(-MAX_LOG_LINES));
      return;
    }

    if (value === "/pause") {
      manager.pause();
      setLogs((prev) => [...prev, "paused active loop"].slice(-MAX_LOG_LINES));
      return;
    }

    if (value === "/resume") {
      manager.resume();
      setLogs((prev) => [...prev, "resumed active loop"].slice(-MAX_LOG_LINES));
      return;
    }

    if (value === "/scenarios") {
      setLogs((prev) => [
        ...prev,
        `scenarios: ${manager.listScenarios().join(", ")}`,
      ].slice(-MAX_LOG_LINES));
      return;
    }

    if (value.startsWith("/run ")) {
      const [, scenario = "grid_ctf", gensText = "5"] = value.split(/\s+/, 3);
      const generations = Number.parseInt(gensText, 10);
      try {
        const runId = await manager.startRun(scenario, Number.isFinite(generations) ? generations : 5);
        setLogs((prev) => [...prev, `accepted run ${runId}`].slice(-MAX_LOG_LINES));
      } catch (err) {
        setLogs((prev) => [...prev, err instanceof Error ? err.message : String(err)].slice(-MAX_LOG_LINES));
      }
      return;
    }

    if (value.startsWith("/hint ")) {
      manager.injectHint(value.slice("/hint ".length).trim());
      setLogs((prev) => [...prev, "operator hint queued"].slice(-MAX_LOG_LINES));
      return;
    }

    if (value.startsWith("/gate ")) {
      const decision = value.slice("/gate ".length).trim();
      if (decision === "advance" || decision === "retry" || decision === "rollback") {
        manager.overrideGate(decision);
        setLogs((prev) => [...prev, `gate override queued: ${decision}`].slice(-MAX_LOG_LINES));
      } else {
        setLogs((prev) => [...prev, "gate override must be advance|retry|rollback"].slice(-MAX_LOG_LINES));
      }
      return;
    }

    if (value.startsWith("/chat ")) {
      const [, role = "analyst", ...rest] = value.split(/\s+/);
      const message = rest.join(" ").trim();
      if (!message) {
        setLogs((prev) => [...prev, "chat command requires a role and message"].slice(-MAX_LOG_LINES));
        return;
      }
      try {
        const response = await manager.chatAgent(role, message);
        const firstLine = response.split("\n")[0] ?? response;
        setLogs((prev) => [...prev, `[${role}] ${firstLine}`].slice(-MAX_LOG_LINES));
      } catch (err) {
        setLogs((prev) => [...prev, err instanceof Error ? err.message : String(err)].slice(-MAX_LOG_LINES));
      }
      return;
    }

    setLogs((prev) => [...prev, "unknown command; use /help"].slice(-MAX_LOG_LINES));
  };

  return (
    <Box flexDirection="column">
      <Box borderStyle="round" paddingX={1} flexDirection="column">
        <Text bold>AutoContext Interactive TUI</Text>
        <Text>server: {serverUrl}</Text>
        <Text>
          run: {state.runId ?? "none"} • scenario: {state.scenario ?? "none"} • status: {statusText}
        </Text>
        <Text dimColor>Ctrl+C exits. Use /help for commands.</Text>
      </Box>

      <Box marginTop={1} borderStyle="round" paddingX={1} flexDirection="column">
        <Text bold>Recent Activity</Text>
        {logs.map((line, idx) => (
          <Text key={`${idx}-${line}`}>{line}</Text>
        ))}
      </Box>

      <Box marginTop={1} borderStyle="round" paddingX={1}>
        <Text color="cyan">{">"} </Text>
        <TextInput value={input} onChange={setInput} onSubmit={(value) => { void submit(value); }} />
      </Box>
    </Box>
  );
}
