import React, { useEffect, useState, useCallback } from "react";
import { Box, Text, useInput, useApp } from "ink";
import TextInput from "ink-text-input";
import { useWebSocket } from "./hooks/useWebSocket.js";
import { useRunState, parseCommand } from "./hooks/useRunState.js";
import { Header } from "./components/Header.js";
import { GenerationBar } from "./components/GenerationBar.js";
import { AgentPanel } from "./components/AgentPanel.js";
import { TrajectoryTable } from "./components/TrajectoryTable.js";
import { TournamentBar } from "./components/TournamentBar.js";
import { GateDecisionDisplay } from "./components/GateDecision.js";
import { ChatPanel } from "./components/ChatPanel.js";
import { ControlBar } from "./components/ControlBar.js";
import { LogTail } from "./components/LogTail.js";
import { ScenarioCreator } from "./components/ScenarioCreator.js";
import type { GateDecision } from "./types.js";

interface AppProps {
  url: string;
}

const GATE_OPTIONS: GateDecision[] = ["advance", "retry", "rollback"];

function HintInput({
  value,
  onChange,
  onSubmit,
}: {
  value: string;
  onChange: (v: string) => void;
  onSubmit: (v: string) => void;
}) {
  return <TextInput value={value} onChange={onChange} onSubmit={onSubmit} />;
}

export function App({ url }: AppProps) {
  const { connected, send, lastMessage } = useWebSocket(url);
  const { state, dispatch } = useRunState();

  // UI mode state
  const [chatInput, setChatInput] = useState("");
  const [hintMode, setHintMode] = useState(false);
  const [hintInput, setHintInput] = useState("");
  const [gateOverrideActive, setGateOverrideActive] = useState(false);
  const [gateSelection, setGateSelection] = useState(0);
  const [chatActive, setChatActive] = useState(false);
  const [revisionMode, setRevisionMode] = useState(false);
  const [revisionInput, setRevisionInput] = useState("");

  const { exit } = useApp();

  // Sync connected state
  useEffect(() => {
    dispatch({ type: "SET_CONNECTED", connected });
  }, [connected, dispatch]);

  // Process incoming messages
  useEffect(() => {
    if (lastMessage) {
      dispatch({ type: "SERVER_MESSAGE", message: lastMessage });
    }
  }, [lastMessage, dispatch]);

  // Keyboard input handling
  useInput(
    useCallback(
      (input: string, key) => {
        // Ctrl+C: quit
        if (input === "c" && key.ctrl) {
          exit();
          return;
        }

        // Scenario creation keyboard handling
        const scPhase = state.scenarioCreation.phase;
        if (scPhase !== "idle" && !revisionMode) {
          if (scPhase === "preview") {
            if (key.return) {
              send({ type: "confirm_scenario" });
              dispatch({ type: "ADD_LOG", line: "Confirming scenario..." });
              return;
            }
            if (input === "r" && !key.ctrl && !key.meta) {
              setRevisionMode(true);
              setRevisionInput("");
              return;
            }
            if (key.escape) {
              send({ type: "cancel_scenario" });
              dispatch({ type: "RESET_SCENARIO_CREATION" });
              dispatch({ type: "ADD_LOG", line: "Scenario creation cancelled" });
              return;
            }
            return;
          }
          if (scPhase === "ready" || scPhase === "error") {
            if (key.escape) {
              dispatch({ type: "RESET_SCENARIO_CREATION" });
              return;
            }
            return;
          }
          // generating/confirming phases: ignore most input
          return;
        }

        // Ctrl+P: toggle pause/resume
        if (input === "p" && key.ctrl) {
          send(state.paused ? { type: "resume" } : { type: "pause" });
          return;
        }

        // Ctrl+G: toggle gate override
        if (input === "g" && key.ctrl) {
          setGateOverrideActive((prev) => !prev);
          setGateSelection(0);
          return;
        }

        // Ctrl+H: toggle hint mode
        if (input === "h" && key.ctrl) {
          setHintMode((prev) => !prev);
          setHintInput("");
          setChatActive(false);
          return;
        }

        // Gate override navigation
        if (gateOverrideActive) {
          if (key.leftArrow) {
            setGateSelection((s) => Math.max(0, s - 1));
            return;
          }
          if (key.rightArrow) {
            setGateSelection((s) => Math.min(GATE_OPTIONS.length - 1, s + 1));
            return;
          }
          if (key.return) {
            send({ type: "override_gate", decision: GATE_OPTIONS[gateSelection]! });
            setGateOverrideActive(false);
            dispatch({
              type: "ADD_LOG",
              line: `Gate override: ${GATE_OPTIONS[gateSelection]!.toUpperCase()}`,
            });
            return;
          }
          if (key.escape) {
            setGateOverrideActive(false);
            return;
          }
          return;
        }

        // Tab: cycle chat target
        if (key.tab) {
          dispatch({ type: "CYCLE_CHAT_TARGET" });
          setChatActive(true);
          return;
        }

        // Escape: exit special modes
        if (key.escape) {
          if (revisionMode) {
            setRevisionMode(false);
            setRevisionInput("");
            return;
          }
          setHintMode(false);
          setChatActive(false);
          return;
        }

        // If not in any special mode, activate chat on any printable key
        if (!hintMode && !chatActive && input.length === 1 && !key.ctrl && !key.meta) {
          setChatActive(true);
          setChatInput(input);
        }
      },
      [state.paused, state.scenarioCreation.phase, gateOverrideActive, gateSelection, hintMode, chatActive, revisionMode, send, dispatch, exit],
    ),
  );

  // Chat submit handler — supports /run and /scenarios commands
  const handleChatSubmit = useCallback(
    (value: string) => {
      if (!value.trim()) return;

      const cmd = parseCommand(value.trim());
      if (cmd) {
        if (cmd.type === "start_run") {
          send({ type: "start_run", scenario: cmd.scenario, generations: cmd.generations });
          dispatch({ type: "ADD_LOG", line: `Starting run: ${cmd.scenario} (${cmd.generations} gens)` });
        } else if (cmd.type === "list_scenarios") {
          send({ type: "list_scenarios" });
          dispatch({ type: "ADD_LOG", line: "Requesting scenario list..." });
        } else if (cmd.type === "create_scenario") {
          send({ type: "create_scenario", description: cmd.description });
          dispatch({ type: "ADD_LOG", line: `Creating scenario: ${cmd.description.slice(0, 60)}` });
        } else {
          dispatch({ type: "ADD_LOG", line: `Unknown command: ${cmd.text}` });
        }
        setChatInput("");
        return;
      }

      send({ type: "chat_agent", role: state.chatTarget, message: value.trim() });
      dispatch({ type: "ADD_USER_CHAT", text: value.trim(), target: state.chatTarget });
      setChatInput("");
    },
    [send, state.chatTarget, dispatch],
  );

  // Revision submit handler for scenario creation
  const handleRevisionSubmit = useCallback(
    (value: string) => {
      if (!value.trim()) return;
      send({ type: "revise_scenario", feedback: value.trim() });
      dispatch({ type: "ADD_LOG", line: `Revising scenario: ${value.trim().slice(0, 60)}` });
      setRevisionInput("");
      setRevisionMode(false);
    },
    [send, dispatch],
  );

  // Hint submit handler
  const handleHintSubmit = useCallback(
    (value: string) => {
      if (!value.trim()) return;
      send({ type: "inject_hint", text: value.trim() });
      dispatch({ type: "ADD_LOG", line: `Hint injected: ${value.trim().slice(0, 60)}` });
      setHintInput("");
      setHintMode(false);
    },
    [send, dispatch],
  );

  return (
    <Box flexDirection="column" width="100%">
      {/* Header */}
      <Header
        connected={state.connected}
        runId={state.runId}
        scenario={state.scenario}
        paused={state.paused}
        currentExecutor={state.currentExecutor}
        agentProvider={state.agentProvider}
      />

      {/* Generation progress bar */}
      <GenerationBar
        currentGeneration={state.currentGeneration}
        totalGenerations={state.totalGenerations}
        generationStartedAt={state.generationStartedAt}
        phase={state.phase}
      />

      {/* Middle section: Agents + Trajectory side by side */}
      <Box>
        <Box width="50%">
          <AgentPanel roles={state.roles} />
        </Box>
        <Box width="50%">
          <TrajectoryTable trajectory={state.trajectory} />
        </Box>
      </Box>

      {/* Tournament + Gate row */}
      <Box borderStyle="single" justifyContent="space-between">
        <TournamentBar tournament={state.tournament} />
        <GateDecisionDisplay
          gateDecision={state.gateDecision}
          gateDelta={state.gateDelta}
          curatorDecision={state.curatorDecision}
          overrideActive={gateOverrideActive}
          overrideSelection={gateSelection}
        />
      </Box>

      {/* Scenario creator panel (replaces chat when active) */}
      {state.scenarioCreation.phase !== "idle" ? (
        <Box flexDirection="column">
          <ScenarioCreator scenarioCreation={state.scenarioCreation} />
          {revisionMode && (
            <Box borderStyle="single" paddingX={1} flexDirection="column">
              <Box>
                <Text color="yellow" bold>
                  Revision feedback:
                </Text>
                <Text> {">"} </Text>
                <HintInput
                  value={revisionInput}
                  onChange={setRevisionInput}
                  onSubmit={handleRevisionSubmit}
                />
              </Box>
              <Text dimColor>Enter to send, Esc to cancel</Text>
            </Box>
          )}
        </Box>
      ) : /* Chat panel or hint input */
      hintMode ? (
        <Box borderStyle="single" paddingX={1} flexDirection="column">
          <Box>
            <Text color="yellow" bold>
              Inject Hint:
            </Text>
            <Text> {">"} </Text>
            <HintInput
              value={hintInput}
              onChange={setHintInput}
              onSubmit={handleHintSubmit}
            />
          </Box>
          <Text dimColor>Enter to send, Esc to cancel</Text>
        </Box>
      ) : (
        <ChatPanel
          messages={state.chatMessages}
          chatTarget={state.chatTarget}
          inputValue={chatInput}
          onInputChange={setChatInput}
          onSubmit={handleChatSubmit}
          inputActive={chatActive}
          hasRun={state.runId !== null}
          scenarios={state.scenarios}
          executors={state.executors}
          currentExecutor={state.currentExecutor}
          agentProvider={state.agentProvider}
        />
      )}

      {/* Log tail */}
      <LogTail logLines={state.logLines} />

      {/* Control bar */}
      <ControlBar paused={state.paused} hintActive={hintMode} />
    </Box>
  );
}
