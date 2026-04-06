/** Environment snapshot redaction (AC-503). */

import type { EnvironmentSnapshot } from "./snapshot.js";

export interface RedactionConfig {
  redactHostname: boolean;
  redactUsername: boolean;
  redactPaths: boolean;
}

export const DEFAULT_REDACTION: RedactionConfig = {
  redactHostname: true,
  redactUsername: true,
  redactPaths: true,
};

const REDACTED = "[REDACTED]";

export function redactSnapshot(
  snapshot: EnvironmentSnapshot,
  config: RedactionConfig = DEFAULT_REDACTION,
): EnvironmentSnapshot {
  const redactedFields: string[] = [];
  let { hostname, username, workingDirectory } = snapshot;
  let { shell } = snapshot;
  let notableFiles = [...snapshot.notableFiles];

  if (config.redactHostname && hostname) {
    hostname = REDACTED;
    redactedFields.push("hostname");
  }
  if (config.redactUsername && username) {
    username = REDACTED;
    redactedFields.push("username");
  }
  if (config.redactPaths && workingDirectory) {
    const prefix = snapshot.workingDirectory;
    workingDirectory = ".";
    notableFiles = notableFiles.map((f) =>
      f.startsWith(prefix) ? `.${f.slice(prefix.length)}` : f,
    );
    redactedFields.push("workingDirectory");
  }
  if (config.redactPaths && shell) {
    const redactedShell = redactPathLike(shell);
    if (redactedShell !== shell) {
      shell = redactedShell;
      redactedFields.push("shell");
    }
  }

  return {
    ...snapshot,
    hostname,
    username,
    workingDirectory,
    shell,
    notableFiles,
    redactedFields,
  };
}

function redactPathLike(value: string): string {
  if (value.startsWith("/")) {
    return value.split("/").filter(Boolean).at(-1) ?? value;
  }
  if (/^[A-Za-z]:[\\/]/.test(value)) {
    return value.split(/[/\\]/).at(-1) ?? value;
  }
  return value;
}
