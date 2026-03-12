import React from "react";
import { Box, Text } from "ink";

interface HeaderProps {
  connected: boolean;
  runId: string | null;
  scenario: string | null;
  paused: boolean;
  currentExecutor: string | null;
  agentProvider: string | null;
}

export function Header({ connected, runId, scenario, paused, currentExecutor, agentProvider }: HeaderProps) {
  return (
    <Box
      borderStyle="single"
      borderBottom={false}
      paddingX={1}
      justifyContent="space-between"
    >
      <Box gap={2}>
        <Text bold color="cyan">
          AutoContext Live
        </Text>
        <Text dimColor>{runId ?? "no run"}</Text>
        <Text dimColor>{scenario ?? ""}</Text>
        {currentExecutor && <Text dimColor>{currentExecutor}</Text>}
        {agentProvider && <Text dimColor>{agentProvider}</Text>}
      </Box>
      <Box gap={2}>
        {paused && (
          <Text color="yellow" bold>
            PAUSED
          </Text>
        )}
        <Text>
          {connected ? (
            <Text color="green">● connected</Text>
          ) : (
            <Text color="red">● disconnected</Text>
          )}
        </Text>
      </Box>
    </Box>
  );
}
