import React, { useEffect, useMemo, useState } from "react";
import { Box, Text, useApp, useInput } from "ink";
import TextInput from "ink-text-input";
import type { RunManager, RunManagerState } from "../server/run-manager.js";
import type { EventCallback } from "../loop/events.js";
import {
  handleInteractiveTuiCommand,
  type PendingLoginState,
} from "./commands.js";
import {
  formatTuiActivitySettings,
  summarizeTuiEvent,
  type TuiActivitySettings,
} from "./activity-summary.js";
import { loadTuiActivitySettings } from "./activity-settings-store.js";
import { buildInitialTuiLogLines } from "./startup-log.js";
import { resolveConfigDir } from "../config/index.js";

interface InteractiveTuiProps {
  manager: RunManager;
  serverUrl: string;
}

const MAX_LOG_LINES = 18;

export function InteractiveTui({ manager, serverUrl }: InteractiveTuiProps) {
  const { exit } = useApp();
  const configDir = useMemo(() => resolveConfigDir(), []);
  const [state, setState] = useState<RunManagerState>(manager.getState());
  const [input, setInput] = useState("");
  const [pendingLogin, setPendingLogin] = useState<PendingLoginState | null>(null);
  const loadedActivitySettings = useMemo(() => loadTuiActivitySettings(configDir), [configDir]);
  const [activitySettings, setActivitySettings] = useState<TuiActivitySettings>(
    loadedActivitySettings,
  );
  const [logs, setLogs] = useState<string[]>(() =>
    buildInitialTuiLogLines({
      serverUrl,
      scenarios: manager.listScenarios(),
      activitySettings: loadedActivitySettings,
    }),
  );

  useEffect(() => {
    const handleState = (next: RunManagerState) => {
      setState(next);
    };
    const handleEvent: EventCallback = (event, payload) => {
      const line = summarizeTuiEvent(event, payload, activitySettings);
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
  }, [activitySettings, manager]);

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
    setInput("");
    const result = await handleInteractiveTuiCommand({
      manager,
      configDir,
      raw,
      pendingLogin,
      activitySettings,
    });
    setPendingLogin(result.pendingLogin);
    if (result.activitySettings) {
      setActivitySettings(result.activitySettings);
    }
    if (result.logLines.length > 0) {
      setLogs((prev) => [...prev, ...result.logLines].slice(-MAX_LOG_LINES));
    }
    if (result.shouldExit) {
      exit();
    }
  };

  return (
    <Box flexDirection="column">
      <Box borderStyle="round" paddingX={1} flexDirection="column">
        <Text bold>autocontext Interactive TUI</Text>
        <Text>server: {serverUrl}</Text>
        <Text>
          run: {state.runId ?? "none"} • scenario: {state.scenario ?? "none"} • status: {statusText}
        </Text>
        <Text dimColor>Ctrl+C exits. Use /help for commands.</Text>
      </Box>

      <Box marginTop={1} borderStyle="round" paddingX={1} flexDirection="column">
        <Text bold>Recent Activity</Text>
        <Text dimColor>{formatTuiActivitySettings(activitySettings)}</Text>
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
