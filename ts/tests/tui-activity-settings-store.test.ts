import { existsSync, mkdtempSync, readFileSync, writeFileSync } from "node:fs";
import { tmpdir } from "node:os";
import { join } from "node:path";
import { describe, expect, it } from "vitest";

import { DEFAULT_TUI_ACTIVITY_SETTINGS } from "../src/tui/activity-summary.js";
import {
  loadTuiActivitySettings,
  resetTuiActivitySettings,
  saveTuiActivitySettings,
  TUI_SETTINGS_FILE,
} from "../src/tui/activity-settings-store.js";

describe("TUI activity settings store", () => {
  it("returns default activity settings when no TUI settings file exists", () => {
    const configDir = mkdtempSync(join(tmpdir(), "tui-settings-missing-"));

    expect(loadTuiActivitySettings(configDir)).toEqual(DEFAULT_TUI_ACTIVITY_SETTINGS);
  });

  it("persists activity settings in the resolved config directory", () => {
    const configDir = mkdtempSync(join(tmpdir(), "tui-settings-save-"));

    saveTuiActivitySettings(configDir, {
      filter: "children",
      verbosity: "verbose",
    });

    const settingsPath = join(configDir, TUI_SETTINGS_FILE);
    expect(existsSync(settingsPath)).toBe(true);
    expect(loadTuiActivitySettings(configDir)).toEqual({
      filter: "children",
      verbosity: "verbose",
    });

    const persisted = JSON.parse(readFileSync(settingsPath, "utf-8"));
    expect(persisted).toMatchObject({
      activity: {
        filter: "children",
        verbosity: "verbose",
      },
    });
    expect(typeof persisted.updatedAt).toBe("string");
  });

  it("resets persisted activity settings back to defaults", () => {
    const configDir = mkdtempSync(join(tmpdir(), "tui-settings-reset-"));
    const settingsPath = join(configDir, TUI_SETTINGS_FILE);
    saveTuiActivitySettings(configDir, {
      filter: "children",
      verbosity: "verbose",
    });

    expect(existsSync(settingsPath)).toBe(true);
    expect(resetTuiActivitySettings(configDir)).toEqual(DEFAULT_TUI_ACTIVITY_SETTINGS);

    expect(existsSync(settingsPath)).toBe(false);
    expect(loadTuiActivitySettings(configDir)).toEqual(DEFAULT_TUI_ACTIVITY_SETTINGS);
  });

  it("falls back per field when persisted activity settings are invalid", () => {
    const configDir = mkdtempSync(join(tmpdir(), "tui-settings-invalid-"));
    writeFileSync(
      join(configDir, TUI_SETTINGS_FILE),
      JSON.stringify({
        activity: {
          filter: "chatter",
          verbosity: "quiet",
        },
      }),
      "utf-8",
    );

    expect(loadTuiActivitySettings(configDir)).toEqual({
      filter: "all",
      verbosity: "quiet",
    });
  });
});
