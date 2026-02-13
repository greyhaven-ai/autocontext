import React from "react";
import { Box, Text } from "ink";
import type { TournamentState } from "../types.js";

interface TournamentBarProps {
  tournament: TournamentState;
}

function progressBar(current: number, total: number, width: number): string {
  if (total === 0) return "\u2591".repeat(width);
  const pct = Math.min(current / total, 1);
  const filled = Math.round(pct * width);
  const empty = width - filled;
  return "\u2588".repeat(filled) + "\u2591".repeat(empty);
}

export function TournamentBar({ tournament }: TournamentBarProps) {
  const { totalMatches, completedMatches, scores } = tournament;

  return (
    <Box paddingX={1} gap={2}>
      <Text bold>Tournament:</Text>
      <Text>
        match {completedMatches}/{totalMatches}
      </Text>
      <Text>{progressBar(completedMatches, totalMatches, 12)}</Text>
      {scores.length > 0 && (
        <Text dimColor>
          scores: [{scores.map((s) => s.toFixed(2)).join(", ")}]
        </Text>
      )}
    </Box>
  );
}
