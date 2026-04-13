import type { ActionDict } from "./action-filter-contracts.js";

export function isContinuousParamSpace(actions: ActionDict[]): boolean {
  if (actions.length === 0) {
    return false;
  }
  return actions.every((action) => {
    if (action.type !== "continuous") {
      return false;
    }
    if (!action.range || action.range.length !== 2) {
      return false;
    }
    const [low, high] = action.range;
    return typeof low === "number" && typeof high === "number";
  });
}

export function formatActionPrompt(actions: ActionDict[]): string {
  if (actions.length === 0) {
    return "No actions available.";
  }
  if (isContinuousParamSpace(actions)) {
    const lines: string[] = ["Provide a JSON object with all strategy parameters:"];
    const example: Record<string, number> = {};
    for (const action of actions) {
      const name = action.action;
      const description = action.description ?? "";
      const [low, high] = action.range!;
      lines.push(`- ${name}: ${description} (range [${low}, ${high}])`);
      example[name] = Number(((low + high) / 2).toFixed(3));
    }
    lines.push(`Example: ${JSON.stringify(example)}`);
    lines.push("Respond with JSON only.");
    return lines.join("\n");
  }

  const lines: string[] = ["Available actions:"];
  for (let index = 0; index < actions.length; index++) {
    const action = actions[index];
    const name = action.action ?? `action_${index + 1}`;
    const description = action.description ?? "";
    let extra = "";
    if (action.type === "continuous" && action.range) {
      extra = ` (continuous [${action.range[0]}, ${action.range[1]}])`;
    } else if (action.row !== undefined && action.col !== undefined) {
      extra = ` (row ${action.row}, col ${action.col})`;
    }

    let line = `${index + 1}. ${name}`;
    if (description) {
      line += ` — ${description}`;
    }
    line += extra;
    lines.push(line);
  }
  lines.push("Select an action by number:");
  return lines.join("\n");
}
