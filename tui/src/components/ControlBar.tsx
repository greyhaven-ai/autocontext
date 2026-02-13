import React from "react";
import { Box, Text } from "ink";

interface ControlBarProps {
  paused: boolean;
  hintActive: boolean;
}

export function ControlBar({ paused, hintActive }: ControlBarProps) {
  return (
    <Box paddingX={1} gap={1} borderStyle="single" borderTop={false}>
      <Text dimColor>
        <Text color="cyan" bold>
          ^P
        </Text>{" "}
        {paused ? "resume" : "pause"}
      </Text>
      <Text dimColor>|</Text>
      <Text dimColor>
        <Text color="cyan" bold>
          ^G
        </Text>{" "}
        gate
      </Text>
      <Text dimColor>|</Text>
      <Text dimColor>
        <Text color="cyan" bold>
          ^H
        </Text>{" "}
        {hintActive ? "send hint" : "hint"}
      </Text>
      <Text dimColor>|</Text>
      <Text dimColor>
        <Text color="cyan" bold>
          Tab
        </Text>{" "}
        agent
      </Text>
      <Text dimColor>|</Text>
      <Text dimColor>
        <Text color="cyan" bold>
          /run
        </Text>{" "}
        start
      </Text>
      <Text dimColor>|</Text>
      <Text dimColor>
        <Text color="cyan" bold>
          ^C
        </Text>{" "}
        quit
      </Text>
    </Box>
  );
}
