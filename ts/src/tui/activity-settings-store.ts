import { existsSync, mkdirSync, readFileSync, unlinkSync, writeFileSync } from "node:fs";
import { join } from "node:path";

import {
  DEFAULT_TUI_ACTIVITY_SETTINGS,
  isTuiActivityFilter,
  isTuiActivityVerbosity,
  type TuiActivitySettings,
} from "./activity-summary.js";

export const TUI_SETTINGS_FILE = "tui-settings.json";

interface TuiSettingsFile {
  readonly activity: TuiActivitySettings;
  readonly updatedAt: string;
}

export function loadTuiActivitySettings(configDir: string): TuiActivitySettings {
  const settingsPath = join(configDir, TUI_SETTINGS_FILE);
  if (!existsSync(settingsPath)) {
    return DEFAULT_TUI_ACTIVITY_SETTINGS;
  }

  try {
    const raw = JSON.parse(readFileSync(settingsPath, "utf-8"));
    const record = readRecord(raw);
    const activity = readRecord(record.activity);
    return {
      filter: readActivityFilter(activity.filter),
      verbosity: readActivityVerbosity(activity.verbosity),
    };
  } catch {
    return DEFAULT_TUI_ACTIVITY_SETTINGS;
  }
}

export function saveTuiActivitySettings(
  configDir: string,
  settings: TuiActivitySettings,
): void {
  mkdirSync(configDir, { recursive: true });
  const payload: TuiSettingsFile = {
    activity: settings,
    updatedAt: new Date().toISOString(),
  };
  writeFileSync(
    join(configDir, TUI_SETTINGS_FILE),
    `${JSON.stringify(payload, null, 2)}\n`,
    "utf-8",
  );
}

export function resetTuiActivitySettings(configDir: string): TuiActivitySettings {
  const settingsPath = join(configDir, TUI_SETTINGS_FILE);
  if (existsSync(settingsPath)) {
    unlinkSync(settingsPath);
  }
  return DEFAULT_TUI_ACTIVITY_SETTINGS;
}

function readActivityFilter(value: unknown): TuiActivitySettings["filter"] {
  return isTuiActivityFilter(value) ? value : DEFAULT_TUI_ACTIVITY_SETTINGS.filter;
}

function readActivityVerbosity(value: unknown): TuiActivitySettings["verbosity"] {
  return isTuiActivityVerbosity(value) ? value : DEFAULT_TUI_ACTIVITY_SETTINGS.verbosity;
}

function readRecord(value: unknown): Record<string, unknown> {
  return isRecord(value) ? value : {};
}

function isRecord(value: unknown): value is Record<string, unknown> {
  return typeof value === "object" && value !== null && !Array.isArray(value);
}
