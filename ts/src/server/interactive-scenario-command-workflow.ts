import type { ClientMessage, ServerMessage } from "./protocol.js";
import type { ScenarioPreviewInfo, ScenarioReadyInfo } from "./run-manager.js";

export function buildScenarioPreviewMessage(preview: ScenarioPreviewInfo): ServerMessage {
  return {
    type: "scenario_preview",
    name: preview.name,
    display_name: preview.displayName,
    description: preview.description,
    strategy_params: preview.strategyParams,
    scoring_components: preview.scoringComponents,
    constraints: preview.constraints,
    win_threshold: preview.winThreshold,
  };
}

export function buildScenarioReadyMessage(ready: ScenarioReadyInfo): ServerMessage {
  return {
    type: "scenario_ready",
    name: ready.name,
    test_scores: ready.testScores,
  };
}

export async function executeInteractiveScenarioCommand(opts: {
  command: Extract<
    ClientMessage,
    { type: "create_scenario" | "revise_scenario" | "confirm_scenario" | "cancel_scenario" }
  >;
  runManager: Pick<
    typeof import("./run-manager.js").RunManager.prototype,
    "createScenario" | "reviseScenario" | "confirmScenario" | "cancelScenario"
  >;
}): Promise<ServerMessage[]> {
  switch (opts.command.type) {
    case "create_scenario": {
      const preview = await opts.runManager.createScenario(opts.command.description);
      return [
        { type: "scenario_generating", name: "custom_scenario" },
        buildScenarioPreviewMessage(preview),
      ];
    }
    case "revise_scenario": {
      const preview = await opts.runManager.reviseScenario(opts.command.feedback);
      return [
        { type: "scenario_generating", name: "custom_scenario" },
        buildScenarioPreviewMessage(preview),
      ];
    }
    case "confirm_scenario": {
      const ready = await opts.runManager.confirmScenario();
      return [
        { type: "ack", action: "confirm_scenario" },
        buildScenarioReadyMessage(ready),
      ];
    }
    case "cancel_scenario": {
      opts.runManager.cancelScenario();
      return [{ type: "ack", action: "cancel_scenario" }];
    }
    default:
      throw new Error(`Unsupported interactive scenario command: ${String((opts.command as { type?: unknown }).type ?? "unknown")}`);
  }
}
