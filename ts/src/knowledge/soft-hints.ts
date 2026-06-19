export type HintStyle = "default" | "structural" | "solution_like";

export const STRUCTURAL_HINT_POLICY =
  "Structural hint policy: prefer constraints, invariants, verification checks, promising representations, and repair directions; avoid full target solutions, exact parameter recipes, and route-locking commitments unless the user explicitly asks.";

const ROUTE_TERMS = ["exact", "set ", "must use", "full solution", "parameter recipe", "route"];
const STRUCTURAL_TERMS = ["constraint", "invariant", "check", "verify", "repair", "representation"];

export function effectiveHintStyle(softHintsEnabled: boolean, hintStyle: string): HintStyle {
  const normalized = hintStyle.trim().toLowerCase();
  if (softHintsEnabled) return "structural";
  if (normalized === "structural" || normalized === "solution_like") return normalized;
  return "default";
}

export function structuralHintPrompt(hintStyle: string): string {
  return hintStyle === "structural" ? STRUCTURAL_HINT_POLICY : "";
}

export function buildHintMetadata(
  text: string,
  opts: { hintStyle: string; supportEvidence?: string },
): {
  hintStyle: string;
  supportEvidence: string;
  isStructural: boolean;
  routePrescriptive: boolean;
} {
  const lowered = text.toLowerCase();
  const routePrescriptive = ROUTE_TERMS.some((term) => lowered.includes(term));
  const structurallyWorded = STRUCTURAL_TERMS.some((term) => lowered.includes(term));
  return {
    hintStyle: opts.hintStyle,
    supportEvidence: opts.supportEvidence ?? "",
    isStructural: opts.hintStyle === "structural" || structurallyWorded,
    routePrescriptive,
  };
}

export interface HintAbReportRow {
  hintStyle?: string;
  score?: number;
  responseLength?: number;
  novelty?: number;
  rolledBack?: boolean;
  hintAdopted?: boolean;
}

export interface HintAbStyleSummary {
  runCount: number;
  meanScore: number | null;
  meanResponseLength: number | null;
  meanNovelty: number | null;
  rollbackRate: number | null;
  hintAdoptionRate: number | null;
}

export function buildHintAbReport(rows: HintAbReportRow[]): {
  schemaVersion: 1;
  styles: Record<string, HintAbStyleSummary>;
} {
  const grouped = new Map<string, HintAbReportRow[]>();
  for (const row of rows) {
    const style = row.hintStyle ?? "default";
    grouped.set(style, [...(grouped.get(style) ?? []), row]);
  }
  return {
    schemaVersion: 1,
    styles: Object.fromEntries(
      [...grouped.entries()]
        .sort(([left], [right]) => left.localeCompare(right))
        .map(([style, items]) => [style, summarize(items)]),
    ),
  };
}

function summarize(rows: HintAbReportRow[]): HintAbStyleSummary {
  return {
    runCount: rows.length,
    meanScore: mean(rows.map((row) => row.score)),
    meanResponseLength: mean(rows.map((row) => row.responseLength)),
    meanNovelty: mean(rows.map((row) => row.novelty)),
    rollbackRate: rate(rows.map((row) => row.rolledBack)),
    hintAdoptionRate: rate(rows.map((row) => row.hintAdopted)),
  };
}

function mean(values: Array<number | undefined>): number | null {
  const numeric = values.filter(
    (value): value is number => typeof value === "number" && Number.isFinite(value),
  );
  return numeric.length === 0
    ? null
    : numeric.reduce((sum, value) => sum + value, 0) / numeric.length;
}

function rate(values: Array<boolean | undefined>): number | null {
  return values.length === 0 ? null : values.filter(Boolean).length / values.length;
}
