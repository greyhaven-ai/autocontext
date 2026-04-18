// Output formatters for the control-plane CLI.
//
// "json"   — single JSON document on stdout (pipe-to-jq compatible).
// "table"  — ASCII table; designed for tty / human; no fancy dependencies.
// "pretty" — human-readable key/value blocks for scalars + objects, bulleted
//            list for arrays.

export type OutputMode = "json" | "table" | "pretty";

export function formatOutput(value: unknown, mode: OutputMode): string {
  switch (mode) {
    case "json":
      return JSON.stringify(value);
    case "table":
      return renderTable(value);
    case "pretty":
      return renderPretty(value);
  }
}

// ---- table ----

function renderTable(value: unknown): string {
  if (!Array.isArray(value)) {
    // Not a tabular value — fall back to a single-row table keyed by the object.
    if (typeof value === "object" && value !== null) {
      return renderTable([value]);
    }
    return String(value);
  }
  if (value.length === 0) {
    return "(no rows)";
  }
  const columns = columnsOf(value);
  const rows = value.map((row) => columns.map((c) => stringifyCell(pluck(row, c))));
  const widths = columns.map((c, i) =>
    Math.max(c.length, ...rows.map((r) => r[i]!.length)),
  );

  const separator = widths.map((w) => "-".repeat(w)).join("-+-");
  const header = columns.map((c, i) => c.padEnd(widths[i]!)).join(" | ");
  const body = rows.map((row) =>
    row.map((cell, i) => cell.padEnd(widths[i]!)).join(" | "),
  );

  return [header, separator, ...body].join("\n");
}

function columnsOf(rows: readonly unknown[]): string[] {
  const cols = new Set<string>();
  for (const row of rows) {
    if (row !== null && typeof row === "object") {
      for (const k of Object.keys(row as Record<string, unknown>)) {
        cols.add(k);
      }
    }
  }
  return Array.from(cols);
}

function pluck(row: unknown, key: string): unknown {
  if (row === null || typeof row !== "object") return undefined;
  return (row as Record<string, unknown>)[key];
}

function stringifyCell(value: unknown): string {
  if (value === undefined) return "";
  if (value === null) return "null";
  if (typeof value === "string") return value;
  if (typeof value === "number" || typeof value === "boolean") return String(value);
  try {
    return JSON.stringify(value);
  } catch {
    return String(value);
  }
}

// ---- pretty ----

function renderPretty(value: unknown, indent = 0): string {
  const pad = "  ".repeat(indent);
  if (value === null || value === undefined) {
    return `${pad}${String(value)}`;
  }
  if (Array.isArray(value)) {
    if (value.length === 0) return `${pad}(empty)`;
    return value
      .map((item, i) => {
        if (typeof item === "object" && item !== null) {
          return `${pad}- [${i}]\n${renderPretty(item, indent + 1)}`;
        }
        return `${pad}- ${stringifyCell(item)}`;
      })
      .join("\n");
  }
  if (typeof value === "object") {
    const entries = Object.entries(value as Record<string, unknown>);
    if (entries.length === 0) return `${pad}(empty object)`;
    return entries
      .map(([k, v]) => {
        if (v !== null && typeof v === "object") {
          return `${pad}${k}:\n${renderPretty(v, indent + 1)}`;
        }
        return `${pad}${k}: ${stringifyCell(v)}`;
      })
      .join("\n");
  }
  return `${pad}${stringifyCell(value)}`;
}
