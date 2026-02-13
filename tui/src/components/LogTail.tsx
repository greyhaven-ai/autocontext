import React from "react";
import { Box, Text } from "ink";

interface LogTailProps {
  logLines: string[];
  maxLines?: number;
}

export function LogTail({ logLines, maxLines = 10 }: LogTailProps) {
  const visible = logLines.slice(-maxLines);

  return (
    <Box flexDirection="column" paddingX={1}>
      <Text bold dimColor>
        Log
      </Text>
      {visible.length === 0 ? (
        <Text dimColor>Waiting for events...</Text>
      ) : (
        visible.map((line, i) => (
          <Text key={i} dimColor wrap="truncate">
            {line}
          </Text>
        ))
      )}
    </Box>
  );
}
