import React from "react";
import { Box, Text } from "ink";
import type { GateDecision as GateDecisionType, CuratorDecision } from "../types.js";

interface GateDecisionProps {
  gateDecision: GateDecisionType | null;
  gateDelta: number | null;
  curatorDecision: CuratorDecision | null;
  overrideActive: boolean;
  overrideSelection: number;
}

const GATE_OPTIONS: GateDecisionType[] = ["advance", "retry", "rollback"];

function gateColor(gate: GateDecisionType): string {
  switch (gate) {
    case "advance":
      return "green";
    case "retry":
      return "yellow";
    case "rollback":
      return "red";
  }
}

function curatorColor(decision: CuratorDecision): string {
  switch (decision) {
    case "accept":
      return "green";
    case "merge":
      return "yellow";
    case "reject":
      return "red";
  }
}

export function GateDecisionDisplay({
  gateDecision,
  gateDelta,
  curatorDecision,
  overrideActive,
  overrideSelection,
}: GateDecisionProps) {
  return (
    <Box paddingX={1} gap={2}>
      <Text bold>Gate:</Text>
      {gateDecision ? (
        <Text color={gateColor(gateDecision)} bold>
          {gateDecision.toUpperCase()}
        </Text>
      ) : (
        <Text dimColor>--</Text>
      )}
      {gateDelta !== null && (
        <Text dimColor>(delta={gateDelta.toFixed(3)})</Text>
      )}
      {curatorDecision && (
        <Box gap={1}>
          <Text bold>Curator:</Text>
          <Text color={curatorColor(curatorDecision)} bold>
            {curatorDecision.toUpperCase()}
          </Text>
        </Box>
      )}
      {overrideActive && (
        <Box gap={1}>
          <Text color="cyan">[Override]</Text>
          {GATE_OPTIONS.map((opt, i) => (
            <Text
              key={opt}
              inverse={i === overrideSelection}
              color={gateColor(opt)}
            >
              {" "}
              {opt.toUpperCase()}{" "}
            </Text>
          ))}
          <Text dimColor>(arrows to select, Enter to confirm, Esc to cancel)</Text>
        </Box>
      )}
    </Box>
  );
}
