import React, { useState, useEffect } from "react";
import { Box, Text } from "ink";
import type { ScenarioCreationState } from "../types.js";

interface ScenarioCreatorProps {
  scenarioCreation: ScenarioCreationState;
}

const SPINNER_FRAMES = ["|", "/", "-", "\\"];

function Spinner({ label }: { label: string }) {
  const [frame, setFrame] = useState(0);
  useEffect(() => {
    const timer = setInterval(() => {
      setFrame((f) => (f + 1) % SPINNER_FRAMES.length);
    }, 120);
    return () => clearInterval(timer);
  }, []);
  return (
    <Text>
      <Text color="cyan">{SPINNER_FRAMES[frame]}</Text> {label}
    </Text>
  );
}

export function ScenarioCreator({ scenarioCreation }: ScenarioCreatorProps) {
  const { phase, name, preview, errorMessage, testScores } = scenarioCreation;

  if (phase === "idle") return null;

  return (
    <Box flexDirection="column" borderStyle="single" paddingX={1}>
      <Text bold color="cyan">
        Scenario Creator
      </Text>

      {phase === "generating" && (
        <Box flexDirection="column" marginTop={1}>
          <Spinner label={`Generating scenario${name ? `: ${name}` : ""}...`} />
        </Box>
      )}

      {phase === "preview" && preview && (
        <Box flexDirection="column" marginTop={1}>
          <Text bold>{preview.displayName}</Text>
          <Text dimColor>{preview.description}</Text>

          <Box marginTop={1} flexDirection="column">
            <Text bold color="yellow">Strategy Parameters:</Text>
            {preview.strategyParams.map((p) => (
              <Text key={p.name}>  {p.name}: <Text dimColor>{p.description}</Text></Text>
            ))}
          </Box>

          <Box marginTop={1} flexDirection="column">
            <Text bold color="yellow">Scoring:</Text>
            {preview.scoringComponents.map((comp) => (
              <Text key={comp.name}>  {comp.name} ({(comp.weight * 100).toFixed(0)}%): <Text dimColor>{comp.description}</Text></Text>
            ))}
          </Box>

          {preview.constraints.length > 0 && (
            <Box marginTop={1} flexDirection="column">
              <Text bold color="yellow">Constraints:</Text>
              {preview.constraints.map((c, i) => (
                <Text key={i}>  - {c}</Text>
              ))}
            </Box>
          )}

          <Box marginTop={1}>
            <Text>Win threshold: <Text bold color="green">{preview.winThreshold}</Text></Text>
          </Box>

          <Box marginTop={1} gap={2}>
            <Text dimColor>
              <Text color="cyan" bold>Enter</Text> confirm
            </Text>
            <Text dimColor>
              <Text color="cyan" bold>r</Text> revise
            </Text>
            <Text dimColor>
              <Text color="cyan" bold>Esc</Text> cancel
            </Text>
          </Box>
        </Box>
      )}

      {phase === "confirming" && (
        <Box flexDirection="column" marginTop={1}>
          <Spinner label="Validating and registering scenario..." />
        </Box>
      )}

      {phase === "ready" && (
        <Box flexDirection="column" marginTop={1}>
          <Text color="green" bold>Scenario ready: {name}</Text>
          {testScores && testScores.length > 0 && (
            <Box marginTop={1} flexDirection="column">
              <Text bold color="yellow">Test scores:</Text>
              {testScores.map((score, i) => (
                <Text key={i}>  Match {i + 1}: {score.toFixed(3)}</Text>
              ))}
            </Box>
          )}
          <Box marginTop={1}>
            <Text dimColor>Run with: <Text color="cyan" bold>/run {name} 5</Text></Text>
          </Box>
          <Box marginTop={1}>
            <Text dimColor>
              <Text color="cyan" bold>Esc</Text> dismiss
            </Text>
          </Box>
        </Box>
      )}

      {phase === "error" && (
        <Box flexDirection="column" marginTop={1}>
          <Text color="red" bold>Scenario creation failed</Text>
          {errorMessage && <Text color="red">{errorMessage}</Text>}
          <Box marginTop={1}>
            <Text dimColor>
              <Text color="cyan" bold>Esc</Text> dismiss
            </Text>
          </Box>
        </Box>
      )}
    </Box>
  );
}
