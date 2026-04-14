import { roundToDecimals } from "./number-utils.js";
import type {
  AttributionResult,
  CreditAssignmentRecord,
  CreditPatternComponentSummary,
  CreditPatternSummary,
} from "./credit-assignment.js";

const ROLE_COMPONENT_PRIORITY: Record<string, string[]> = {
  analyst: ["analysis", "playbook", "hints"],
  coach: ["playbook", "hints", "analysis"],
  architect: ["tools"],
  competitor: ["playbook", "hints"],
};

const ROLE_TITLES: Record<string, string> = {
  analyst: "Previous Analysis Attribution",
  coach: "Previous Coaching Attribution",
  architect: "Previous Tooling Attribution",
  competitor: "Previous Strategy Attribution",
};

const ROLE_GUIDANCE: Record<string, string> = {
  analyst: "Use this to focus your next diagnosis on the changes that actually moved score.",
  coach: "Use this to reinforce the coaching changes that translated into measurable gains.",
  architect: "Use this to prioritize tool work only where tooling actually moved outcomes.",
  competitor: "Use this to lean into the strategy surfaces that correlated with progress.",
};

export function formatAttributionForAgent(result: AttributionResult, role: string): string {
  if (Object.keys(result.credits).length === 0 || result.totalDelta <= 0) {
    return "";
  }

  const normalizedRole = role.trim().toLowerCase();
  const title = ROLE_TITLES[normalizedRole] ?? "Credit Attribution";
  const guidance = ROLE_GUIDANCE[normalizedRole] ?? "";
  const preferred = ROLE_COMPONENT_PRIORITY[normalizedRole] ?? [];
  const orderedComponents: string[] = [];

  for (const component of preferred) {
    if (component in result.credits) {
      orderedComponents.push(component);
    }
  }

  const remaining = Object.entries(result.credits)
    .sort((left, right) => {
      if (right[1] !== left[1]) {
        return right[1] - left[1];
      }
      return left[0].localeCompare(right[0]);
    })
    .map(([component]) => component);
  for (const component of remaining) {
    if (!orderedComponents.includes(component)) {
      orderedComponents.push(component);
    }
  }

  const lines = [`## ${title} (Gen ${result.generation})`, `Total score improvement: +${result.totalDelta.toFixed(4)}`];
  if (guidance) {
    lines.push(guidance);
  }
  lines.push("");

  for (const component of orderedComponents) {
    const credit = result.credits[component] ?? 0;
    const share = result.totalDelta > 0 ? (credit / result.totalDelta) * 100 : 0;
    lines.push(`- ${component}: +${credit.toFixed(4)} (${Math.round(share)}% of improvement)`);
  }

  return lines.join("\n");
}

export function summarizeCreditPatterns(
  records: CreditAssignmentRecord[],
): CreditPatternSummary {
  const componentRollup = new Map<string, CreditPatternComponentSummary>();
  const runIds = [...new Set(records.map((record) => record.runId).filter(Boolean))].sort();

  for (const record of records) {
    const totalDelta = Math.max(record.attribution.totalDelta, 0);
    for (const change of record.vector.changes) {
      const bucket = componentRollup.get(change.component) ?? {
        component: change.component,
        generationCount: 0,
        positiveGenerationCount: 0,
        totalCredit: 0,
        totalChangeMagnitude: 0,
        averageCredit: 0,
        averageShare: 0,
      };

      bucket.generationCount += 1;
      bucket.totalChangeMagnitude = roundToDecimals(bucket.totalChangeMagnitude + change.magnitude, 6);

      const credit = Number(record.attribution.credits[change.component] ?? 0);
      if (credit > 0) {
        bucket.positiveGenerationCount += 1;
      }
      bucket.totalCredit = roundToDecimals(bucket.totalCredit + credit, 6);

      if (totalDelta > 0) {
        bucket.averageShare = roundToDecimals(bucket.averageShare + credit / totalDelta, 6);
      }
      componentRollup.set(change.component, bucket);
    }
  }

  const components = [...componentRollup.values()].map((bucket) => {
    const generationCount = bucket.generationCount;
    if (generationCount > 0) {
      bucket.averageCredit = roundToDecimals(bucket.totalCredit / generationCount, 6);
      bucket.averageShare = roundToDecimals(bucket.averageShare / generationCount, 6);
    }
    return { ...bucket };
  });

  components.sort((left, right) => {
    const creditDelta = Number(right.totalCredit) - Number(left.totalCredit);
    if (creditDelta !== 0) {
      return creditDelta;
    }
    return String(left.component).localeCompare(String(right.component));
  });

  return {
    totalRecords: records.length,
    runCount: runIds.length,
    runIds,
    components,
  };
}
