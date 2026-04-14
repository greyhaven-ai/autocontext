/**
 * Rich sweep DSL for simulate (AC-454).
 *
 * Extends the basic min:max:step parser with:
 * - Categorical sweeps: key=val1,val2,val3
 * - Logarithmic scales: key=log:min:max:steps
 * - Sweep file loading: JSON config with dimensions array
 * - Named presets: JSON object keyed by preset name
 */

import { existsSync, readFileSync } from "node:fs";
import { normalizeSimulationSweepValue } from "./score-normalization.js";

// ---------------------------------------------------------------------------
// Types
// ---------------------------------------------------------------------------

export type SweepScale = "linear" | "log" | "categorical";

export interface SweepDimension {
  name: string;
  values: Array<number | string>;
  scale: SweepScale;
}

function parseScalarValue(value: string | number): number | string {
  if (typeof value === "number") {
    return value;
  }

  const trimmed = value.trim();
  if (!trimmed) {
    return trimmed;
  }

  if (/^-?(?:\d+\.?\d*|\.\d+)(?:[eE][+-]?\d+)?$/.test(trimmed)) {
    const numeric = Number(trimmed);
    if (Number.isFinite(numeric)) {
      return numeric;
    }
  }

  return trimmed;
}

// ---------------------------------------------------------------------------
// CLI parser: parseSweepSpec
// ---------------------------------------------------------------------------

/**
 * Parse a sweep spec string from the CLI.
 *
 * Supported formats:
 * - Linear:      key=min:max:step        → [min, min+step, ..., max]
 * - Logarithmic: key=log:min:max:steps   → log-spaced values
 * - Categorical: key=val1,val2,val3      → ["val1", "val2", "val3"]
 *
 * Multiple dimensions separated by commas (when using linear/log)
 * or semicolons for unambiguous multi-dimension specs.
 *
 * Heuristic: if the value part contains ":" it's a range (linear or log).
 * Otherwise it's categorical.
 */
export function parseSweepSpec(input: string): SweepDimension[] {
  if (!input.trim()) return [];

  const dims: SweepDimension[] = [];

  // Split on top-level dimension boundaries.
  // We split by finding key=value pairs. A dimension starts with a word
  // followed by "=". We accumulate until the next dimension starts.
  const rawDims = splitDimensions(input);

  for (const raw of rawDims) {
    const eqIdx = raw.indexOf("=");
    if (eqIdx < 0) continue;
    const name = raw.slice(0, eqIdx).trim();
    const valuePart = raw.slice(eqIdx + 1).trim();
    if (!name || !valuePart) continue;

    // Check for log scale: log:min:max:steps
    if (valuePart.startsWith("log:")) {
      const logDim = parseLogRange(name, valuePart);
      if (logDim) { dims.push(logDim); continue; }
    }

    // Check for linear range: min:max:step (all parts numeric)
    if (valuePart.includes(":")) {
      const linearDim = parseLinearRange(name, valuePart);
      if (linearDim) { dims.push(linearDim); continue; }
    }

    // Otherwise: categorical
    dims.push(parseCategorical(name, valuePart));
  }

  return dims;
}

/**
 * Split input into dimension strings, handling the ambiguity between
 * commas as dimension separators vs categorical value separators.
 *
 * Strategy: a comma followed by a word and "=" starts a new dimension.
 * Otherwise the comma is part of a categorical value list.
 */
function splitDimensions(input: string): string[] {
  const dims: string[] = [];
  const segments = input
    .split(";")
    .map((segment) => segment.trim())
    .filter(Boolean);

  for (const segment of segments) {
    let current = "";
    const parts = segment.split(",");
    for (let i = 0; i < parts.length; i++) {
      const part = parts[i].trim();
      // Does this part look like the start of a new dimension? (has key=...)
      if (current && /^[a-zA-Z_][a-zA-Z0-9_]*=/.test(part)) {
        dims.push(current);
        current = part;
      } else if (!current) {
        current = part;
      } else {
        // Continuation of categorical values
        current += "," + part;
      }
    }
    if (current) dims.push(current);
  }

  return dims;
}

function parseLinearRange(name: string, valuePart: string): SweepDimension | null {
  const parts = valuePart.split(":");
  if (parts.length !== 3) return null;
  const min = Number(parts[0]);
  const max = Number(parts[1]);
  const step = Number(parts[2]);
  if (isNaN(min) || isNaN(max) || isNaN(step) || step <= 0) return null;

  const values: number[] = [];
  for (let v = min; v <= max + step / 2; v += step) {
    values.push(normalizeSimulationSweepValue(v));
  }
  return { name, values, scale: "linear" };
}

function parseLogRange(name: string, valuePart: string): SweepDimension | null {
  // log:min:max:steps
  const parts = valuePart.split(":");
  if (parts.length !== 4 || parts[0] !== "log") return null;
  const min = Number(parts[1]);
  const max = Number(parts[2]);
  const steps = Math.floor(Number(parts[3]));
  if (isNaN(min) || isNaN(max) || isNaN(steps) || min <= 0 || max <= 0 || steps < 2) return null;

  const logMin = Math.log10(min);
  const logMax = Math.log10(max);
  const values: number[] = [];
  for (let i = 0; i < steps; i++) {
    const logVal = logMin + (logMax - logMin) * i / (steps - 1);
    values.push(normalizeSimulationSweepValue(Math.pow(10, logVal)));
  }
  return { name, values, scale: "log" };
}

function parseCategorical(name: string, valuePart: string): SweepDimension {
  const values = valuePart
    .split(",")
    .map((v) => v.trim())
    .filter(Boolean)
    .map(parseScalarValue);
  return { name, values, scale: "categorical" };
}

// ---------------------------------------------------------------------------
// Sweep file loading
// ---------------------------------------------------------------------------

interface SweepFileDimension {
  name: string;
  min?: number;
  max?: number;
  step?: number;
  steps?: number;
  scale?: string;
  values?: Array<string | number>;
}

/**
 * Load sweep configuration from a JSON file.
 *
 * Expected format:
 * {
 *   "dimensions": [
 *     { "name": "threshold", "min": 0.3, "max": 0.9, "step": 0.2 },
 *     { "name": "strategy", "values": ["aggressive", "balanced"] },
 *     { "name": "lr", "min": 0.001, "max": 1.0, "steps": 5, "scale": "log" }
 *   ]
 * }
 */
export function loadSweepFile(filePath: string): SweepDimension[] {
  if (!existsSync(filePath)) {
    throw new Error(`Sweep file not found: ${filePath}`);
  }

  const raw = readFileSync(filePath, "utf-8");
  const config = JSON.parse(raw) as { dimensions: SweepFileDimension[] };

  if (!Array.isArray(config.dimensions)) {
    throw new Error("Sweep file must have a 'dimensions' array");
  }

  return config.dimensions.map((dim) => {
    if (dim.values && Array.isArray(dim.values)) {
      if (dim.values.length === 0) {
        throw new Error(`Sweep dimension '${dim.name}' must define at least one categorical value`);
      }
      return {
        name: dim.name,
        values: dim.values.map(parseScalarValue),
        scale: "categorical" as SweepScale,
      };
    }
    if (dim.scale === "log" || dim.steps != null) {
      if (dim.scale !== "log" || dim.min == null || dim.max == null || dim.steps == null) {
        throw new Error(
          `Invalid log sweep dimension '${dim.name}': expected { min, max, steps, scale: "log" }`,
        );
      }
      const result = parseLogRange(dim.name, `log:${dim.min}:${dim.max}:${dim.steps}`);
      if (!result) {
        throw new Error(`Invalid log sweep dimension '${dim.name}'`);
      }
      return result;
    }
    if (dim.min != null || dim.max != null || dim.step != null) {
      if (dim.min == null || dim.max == null || dim.step == null) {
        throw new Error(
          `Invalid linear sweep dimension '${dim.name}': expected { min, max, step }`,
        );
      }
      const result = parseLinearRange(dim.name, `${dim.min}:${dim.max}:${dim.step}`);
      if (!result) {
        throw new Error(`Invalid linear sweep dimension '${dim.name}'`);
      }
      return result;
    }

    throw new Error(
      `Invalid sweep dimension '${dim.name}': expected a values array, a linear range, or a log range`,
    );
  });
}

// ---------------------------------------------------------------------------
// Presets
// ---------------------------------------------------------------------------

/**
 * Parse a named preset from a JSON string of presets.
 *
 * Presets format: { "presetName": { "key": value, ... }, ... }
 */
export function parsePreset(
  presetName: string,
  presetsJson: string,
): Record<string, unknown> | null {
  try {
    const presets = JSON.parse(presetsJson) as Record<string, Record<string, unknown>>;
    const preset = presets[presetName];
    if (!preset || typeof preset !== "object" || Array.isArray(preset)) {
      return null;
    }
    return preset;
  } catch {
    return null;
  }
}
