import React from "react";
import { Box, Text } from "ink";
import type { Role, RoleState } from "../types.js";

interface AgentPanelProps {
  roles: Record<Role, RoleState>;
}

const ROLE_ORDER: Role[] = ["competitor", "analyst", "coach", "architect", "curator"];

function statusIcon(status: RoleState["status"]): React.ReactNode {
  switch (status) {
    case "done":
      return <Text color="green">done</Text>;
    case "running":
      return <Text color="yellow">run </Text>;
    case "waiting":
      return <Text dimColor>wait</Text>;
    case "n/a":
      return <Text dimColor>--  </Text>;
  }
}

function formatLatency(ms: number | null): string {
  if (ms === null) return "   --";
  return (ms / 1000).toFixed(1).padStart(5) + "s";
}

function formatTokens(tokens: number | null): string {
  if (tokens === null) return "  --";
  if (tokens >= 1000) return (tokens / 1000).toFixed(1).padStart(4) + "k";
  return String(tokens).padStart(5);
}

export function AgentPanel({ roles }: AgentPanelProps) {
  return (
    <Box flexDirection="column" borderStyle="single" paddingX={1}>
      <Text bold>Agents</Text>
      <Box>
        <Text dimColor>
          {"Role".padEnd(12)}{"Status".padEnd(6)}{"Time".padStart(6)}{"Tokens".padStart(6)}
        </Text>
      </Box>
      {ROLE_ORDER.map((role) => {
        const r = roles[role];
        return (
          <Box key={role}>
            <Text>{role.padEnd(12)}</Text>
            <Box width={6}>{statusIcon(r.status)}</Box>
            <Text dimColor>{formatLatency(r.latencyMs)}</Text>
            <Text dimColor>{formatTokens(r.tokens)}</Text>
          </Box>
        );
      })}
    </Box>
  );
}
