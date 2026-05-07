import { formatCommandHelp } from "./commands.js";
import {
  formatTuiActivitySettings,
  type TuiActivitySettings,
} from "./activity-summary.js";

export interface InitialTuiLogInput {
  readonly serverUrl: string;
  readonly scenarios: readonly string[];
  readonly activitySettings: TuiActivitySettings;
}

export function buildInitialTuiLogLines(input: InitialTuiLogInput): string[] {
  return [
    `interactive server: ${input.serverUrl}`,
    `available scenarios: ${input.scenarios.join(", ")}`,
    `loaded ${formatTuiActivitySettings(input.activitySettings)}`,
    ...formatCommandHelp(),
  ];
}
