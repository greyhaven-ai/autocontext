/**
 * `train` command (AC-853 split of command-handlers.ts).
 */
import { emitEngineResult } from "../emit-engine-result.js";
import { errorMessage } from "./shared.js";

// ---------------------------------------------------------------------------
// train command (AC-460 audit fix)
// ---------------------------------------------------------------------------

export async function cmdTrain(): Promise<void> {
  const { parseArgs } = await import("node:util");
  const {
    executeTrainCommandWorkflow,
    planTrainCommand,
    renderTrainSuccess,
    TRAIN_COMMAND_PARSE_OPTIONS,
    TRAIN_HELP_TEXT,
  } = await import("../train-command-workflow.js");
  const { values } = parseArgs({
    args: process.argv.slice(3),
    options: TRAIN_COMMAND_PARSE_OPTIONS,
  });

  if (values.help) {
    console.log(TRAIN_HELP_TEXT);
    process.exit(0);
  }

  const { loadSettings } = await import("../../config/index.js");
  const { resolve } = await import("node:path");
  const settings = loadSettings();

  let plan;
  try {
    plan = planTrainCommand(values, settings.runsRoot, resolve);
  } catch (error) {
    console.error(errorMessage(error));
    process.exit(1);
  }

  const { TrainingRunner } = await import("../../training/backends.js");
  let result;
  try {
    result = await executeTrainCommandWorkflow({
      plan,
      createRunner: () => new TrainingRunner(),
    });
  } catch (error) {
    console.error(errorMessage(error));
    process.exit(1);
  }

  emitEngineResult(result, {
    json: !!values.json,
    label: "Training",
    renderSuccess: (r) => {
      console.log(renderTrainSuccess(r));
    },
  });
}
