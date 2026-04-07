/** Environment snapshot domain model (AC-503). */

export interface PackageInfo {
  name: string;
  version: string;
}

export interface EnvironmentSnapshot {
  workingDirectory: string;
  osName: string;
  osVersion: string;
  shell: string;
  hostname: string;
  username: string;

  pythonVersion: string;
  availableRuntimes: Record<string, string>;

  installedPackages: PackageInfo[];
  lockfilesFound: string[];

  notableFiles: string[];
  directoryCount: number;
  fileCount: number;

  gitBranch: string | null;
  gitCommit: string | null;
  gitDirty: boolean;
  gitWorktree: boolean;

  memoryTotalMb: number;
  memoryAvailableMb: number;
  diskFreeGb: number;
  cpuCount: number;

  collectedAt: string;
  collectorVersion: string;
  redactedFields: string[];
}
