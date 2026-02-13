import React from "react";
import { Box, Text } from "ink";

interface GenerationBarProps {
  currentGeneration: number | null;
  totalGenerations: number | null;
  generationStartedAt: number | null;
  phase: string | null;
}

function formatElapsed(startedAt: number | null): string {
  if (!startedAt) return "--:--";
  const elapsed = Math.floor((Date.now() - startedAt) / 1000);
  const mins = Math.floor(elapsed / 60);
  const secs = elapsed % 60;
  return `${String(mins).padStart(2, "0")}:${String(secs).padStart(2, "0")}`;
}

function progressBar(current: number, total: number, width: number): string {
  const pct = Math.min(current / total, 1);
  const filled = Math.round(pct * width);
  const empty = width - filled;
  return "\u2588".repeat(filled) + "\u2591".repeat(empty);
}

export function GenerationBar({
  currentGeneration,
  totalGenerations,
  generationStartedAt,
  phase,
}: GenerationBarProps) {
  const gen = currentGeneration ?? 0;
  const total = totalGenerations ?? gen;
  const pct = total > 0 ? Math.round((gen / total) * 100) : 0;

  return (
    <Box paddingX={2} gap={2}>
      <Text>
        Generation{" "}
        <Text bold>
          {gen} / {total || "?"}
        </Text>
      </Text>
      <Text>{progressBar(gen, Math.max(total, 1), 20)}</Text>
      <Text dimColor>{pct}%</Text>
      <Text dimColor>{formatElapsed(generationStartedAt)}</Text>
      {phase && <Text color="blue">[{phase}]</Text>}
    </Box>
  );
}
