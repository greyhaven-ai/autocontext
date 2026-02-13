import React from "react";
import { Box, Text } from "ink";
import type { TrajectoryRow, GateDecision } from "../types.js";

interface TrajectoryTableProps {
  trajectory: TrajectoryRow[];
}

function gateColor(gate: GateDecision): string {
  switch (gate) {
    case "advance":
      return "green";
    case "retry":
      return "yellow";
    case "rollback":
      return "red";
  }
}

function gateLabel(gate: GateDecision): string {
  switch (gate) {
    case "advance":
      return "ADV";
    case "retry":
      return "RTY";
    case "rollback":
      return "ROL";
  }
}

export function TrajectoryTable({ trajectory }: TrajectoryTableProps) {
  return (
    <Box flexDirection="column" borderStyle="single" paddingX={1}>
      <Text bold>Score Trajectory</Text>
      <Box>
        <Text dimColor>
          {"Gen".padStart(4)}{"Mean".padStart(7)}{"Best".padStart(7)}{"Elo".padStart(7)}{"Gate".padStart(5)}
        </Text>
      </Box>
      {trajectory.length === 0 ? (
        <Text dimColor>  No data yet</Text>
      ) : (
        trajectory.slice(-10).map((row) => (
          <Box key={row.generation}>
            <Text>{String(row.generation).padStart(4)}</Text>
            <Text>{row.meanScore.toFixed(3).padStart(7)}</Text>
            <Text>{row.bestScore.toFixed(3).padStart(7)}</Text>
            <Text>{row.elo.toFixed(0).padStart(7)}</Text>
            <Text color={gateColor(row.gate)}>
              {gateLabel(row.gate).padStart(5)}
            </Text>
          </Box>
        ))
      )}
    </Box>
  );
}
