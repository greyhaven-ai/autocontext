import { join, resolve } from "node:path";

export const REPLAY_HELP_TEXT = `autoctx replay — Print replay JSON for a generation

Usage: autoctx replay [options]

Options:
  --run-id <id>        Run to replay (required)
  --generation N       Generation number to replay (default: 1)

See also: run, list, export`;

export interface ReplayCommandValues {
  "run-id"?: string;
  generation?: string;
}

export interface ReplayCommandPlan {
  runId: string;
  generation: number;
}

export interface ReplayCommandResult {
  stderr: string;
  stdout: string;
}

export function planReplayCommand(values: ReplayCommandValues): ReplayCommandPlan {
  if (!values["run-id"]) {
    throw new Error("Error: --run-id is required");
  }

  return {
    runId: values["run-id"],
    generation: Number.parseInt(values.generation ?? "1", 10),
  };
}

export function executeReplayCommandWorkflow(opts: {
  runId: string;
  generation: number;
  runsRoot: string;
  existsSync: (path: string) => boolean;
  readdirSync: (path: string) => string[];
  readFileSync: (path: string, encoding: "utf-8") => string;
}): ReplayCommandResult {
  const generationsDir = join(resolve(opts.runsRoot), opts.runId, "generations");
  const availableGenerations = opts.existsSync(generationsDir)
    ? opts.readdirSync(generationsDir)
        .map((name) => {
          const match = /^gen_(\d+)$/.exec(name);
          return match ? Number.parseInt(match[1] ?? "", 10) : null;
        })
        .filter((value): value is number => value !== null)
        .sort((a, b) => a - b)
    : [];
  const replayDir = join(generationsDir, `gen_${opts.generation}`, "replays");
  const available =
    availableGenerations.length > 0
      ? ` Available generations: ${availableGenerations.join(", ")}.`
      : "";

  if (!opts.existsSync(replayDir)) {
    throw new Error(`No replay files found under ${replayDir}.${available}`);
  }

  const replayFiles = opts.readdirSync(replayDir).filter((name) => name.endsWith(".json")).sort();
  if (replayFiles.length === 0) {
    throw new Error(`No replay files found under ${replayDir}.${available}`);
  }

  const payload = JSON.parse(opts.readFileSync(join(replayDir, replayFiles[0]), "utf-8"));
  return {
    stderr: `Replaying generation ${opts.generation}. Available generations: ${availableGenerations.length > 0 ? availableGenerations.join(", ") : String(opts.generation)}`,
    stdout: JSON.stringify(payload, null, 2),
  };
}
