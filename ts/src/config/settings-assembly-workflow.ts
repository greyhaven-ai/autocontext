import process from "node:process";

import type { AppSettings } from "./app-settings-schema.js";
import { AppSettingsSchema } from "./app-settings-schema.js";
import { assertOfflineSupported } from "./offline.js";
import { applyPreset } from "./presets.js";
import type { ProjectConfig } from "./project-config.js";
import {
  buildProjectConfigSettingsOverrides,
  resolveEnvSettingsOverrides,
} from "./settings-resolution.js";

export function getDefaultSettingsRecord(): Record<string, unknown> {
  return AppSettingsSchema.parse({}) as Record<string, unknown>;
}

export function buildSettingsAssemblyInput(opts?: {
  presetName?: string;
  projectConfig?: ProjectConfig | null;
  env?: Record<string, string | undefined>;
  defaults?: Record<string, unknown>;
}): Record<string, unknown> {
  const presetName = opts?.presetName ?? process.env.AUTOCONTEXT_PRESET ?? "";
  const env = opts?.env ?? process.env;

  // AC-938: this engine does not enforce offline mode, so it refuses rather
  // than running unenforced. Checked here because every run assembles settings;
  // a CLI entry point added later would otherwise bypass it silently.
  assertOfflineSupported(env);
  const defaults = opts?.defaults ?? getDefaultSettingsRecord();

  const assembled = {
    ...applyPreset(presetName),
    ...buildProjectConfigSettingsOverrides(opts?.projectConfig ?? null),
    ...resolveEnvSettingsOverrides(defaults, env),
  };

  // Record what was actually supplied before the schema fills in its defaults.
  // This is the only point where that distinction still exists (AC-911).
  return { ...assembled, configuredFields: Object.keys(assembled).sort() };
}

export function parseAppSettings(input: Record<string, unknown>): AppSettings {
  return AppSettingsSchema.parse(input);
}
