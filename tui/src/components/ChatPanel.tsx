import React from "react";
import { Box, Text } from "ink";
import TextInput from "ink-text-input";
import type { ChatMessage, Role, ScenarioInfo, ExecutorInfo } from "../types.js";

interface ChatPanelProps {
  messages: ChatMessage[];
  chatTarget: Role;
  inputValue: string;
  onInputChange: (value: string) => void;
  onSubmit: (value: string) => void;
  inputActive: boolean;
  hasRun: boolean;
  scenarios: ScenarioInfo[];
  executors: ExecutorInfo[];
  currentExecutor: string | null;
  agentProvider: string | null;
}

function senderColor(sender: string): string {
  switch (sender) {
    case "you":
      return "white";
    case "competitor":
      return "blue";
    case "analyst":
      return "magenta";
    case "coach":
      return "green";
    case "architect":
      return "yellow";
    case "curator":
      return "cyan";
    case "system":
      return "red";
    default:
      return "white";
  }
}

function executorStatusColor(available: boolean): string {
  return available ? "green" : "red";
}

export function ChatPanel({
  messages,
  chatTarget,
  inputValue,
  onInputChange,
  onSubmit,
  inputActive,
  hasRun,
  scenarios,
  executors,
  currentExecutor,
  agentProvider,
}: ChatPanelProps) {
  const visible = messages.slice(-8);

  return (
    <Box flexDirection="column" borderStyle="single" paddingX={1} minHeight={6}>
      <Text bold>Chat</Text>
      {visible.length === 0 && !hasRun ? (
        <Box flexDirection="column">
          {/* Scenarios */}
          <Text bold color="cyan">Scenarios</Text>
          {scenarios.length > 0 ? (
            scenarios.map((s) => (
              <Box key={s.name} paddingLeft={1}>
                <Text color="white" bold>{s.name}</Text>
                <Text dimColor> — {s.description.length > 70 ? s.description.slice(0, 70) + "…" : s.description}</Text>
              </Box>
            ))
          ) : (
            <Box paddingLeft={1}><Text dimColor>Loading scenarios...</Text></Box>
          )}

          {/* Execution environments */}
          <Box marginTop={1}>
            <Text bold color="cyan">Executors</Text>
          </Box>
          {executors.map((e) => (
            <Box key={e.mode} paddingLeft={1}>
              <Text color={executorStatusColor(e.available)} bold>
                {e.available ? "●" : "○"} {e.mode}
              </Text>
              {e.mode === currentExecutor && <Text color="yellow"> (active)</Text>}
              <Text dimColor> — {e.description}</Text>
              {e.resources && (
                <Text dimColor> [{e.resources.cpu_cores}cpu, {e.resources.memory_gb}GB, {e.resources.docker_image}]</Text>
              )}
            </Box>
          ))}

          {agentProvider && (
            <Box marginTop={1}>
              <Text dimColor>Provider: </Text>
              <Text color="white" bold>{agentProvider}</Text>
            </Box>
          )}

          {/* Command hints */}
          <Box marginTop={1}>
            <Text dimColor>Start a run:</Text>
          </Box>
          <Text color="cyan">  /run {scenarios.length > 0 ? scenarios[0]!.name : "grid_ctf"} 5</Text>
          <Text dimColor>  /scenarios — refresh environment info</Text>
        </Box>
      ) : visible.length === 0 ? (
        <Text dimColor>No messages yet. Tab to select agent, type to chat.</Text>
      ) : (
        visible.map((msg, i) => (
          <Box key={i}>
            <Text color={senderColor(msg.sender)} bold>
              [{msg.sender}]
            </Text>
            <Text> {msg.text}</Text>
          </Box>
        ))
      )}
      <Box marginTop={1}>
        <Text dimColor>{"─".repeat(50)}</Text>
      </Box>
      <Box>
        <Text color="cyan">[{chatTarget}]</Text>
        <Text> {">"} </Text>
        {inputActive ? (
          <TextInput
            value={inputValue}
            onChange={onInputChange}
            onSubmit={onSubmit}
          />
        ) : (
          <Text dimColor>{inputValue || (hasRun ? "Type to chat..." : "Type /run to start...")}</Text>
        )}
      </Box>
    </Box>
  );
}
