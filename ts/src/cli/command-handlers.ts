/**
 * autocontext CLI — command handler registry.
 *
 * AC-853: command-handlers.ts was split by command family into
 * ./commands/<family>.ts. This file is now a re-export barrel: it wires
 * each family's handlers into the NO_DB_COMMAND_HANDLERS / DB_COMMAND_HANDLERS
 * dispatch tables that cli/index.ts consumes, and re-exports the small set
 * of helpers index.ts imports directly.
 */
import type { DbCommandName, NoDbCommandName } from "./command-registry.js";
import { cmdAgent } from "./commands/agent.js";
import { cmdLogin, cmdLogout, cmdModels, cmdProviders, cmdWhoami } from "./commands/auth.js";
import {
  cmdAnalyze,
  cmdContextSelection,
  cmdInvestigate,
  cmdSimulate,
} from "./commands/analysis.js";
import {
  cmdBenchmark,
  cmdList,
  cmdReplay,
  cmdRun,
  cmdRuntimeSessions,
  cmdShow,
  cmdStatus,
  cmdWatch,
} from "./commands/run.js";
import { cmdBlob } from "./commands/blob.js";
import { cmdCampaign } from "./commands/campaign.js";
import {
  cmdControlPlane,
  cmdInstrument,
  cmdProbes,
  cmdProductionTraces,
  cmdTraceFindings,
} from "./commands/control-plane.js";
import { cmdExport, cmdExportTrainingData, cmdImportPackage } from "./commands/export.js";
import { cmdImprove, cmdJudge, cmdRepl, cmdSolve, cmdTui } from "./commands/evaluate.js";
import { cmdCapabilities, cmdInit } from "./commands/init.js";
import { cmdMission } from "./commands/mission.js";
import { cmdMcpServe, cmdServeHttp } from "./commands/serve.js";
import { cmdQueue, cmdWorker } from "./commands/queue.js";
import { cmdNewScenario, cmdScenario } from "./commands/scenario.js";
import { cmdTrain } from "./commands/train.js";

export { getDbPath, formatFatalCliError, buildProjectConfigSummary } from "./commands/shared.js";
export { cmdControlPlane };

export const NO_DB_COMMAND_HANDLERS: Record<NoDbCommandName, () => Promise<void>> = {
  init: cmdInit,
  capabilities: cmdCapabilities,
  login: cmdLogin,
  whoami: cmdWhoami,
  logout: cmdLogout,
  providers: cmdProviders,
  models: cmdModels,
  agent: cmdAgent,
  train: cmdTrain,
  simulate: cmdSimulate,
  investigate: cmdInvestigate,
  analyze: cmdAnalyze,
  "context-selection": cmdContextSelection,
  blob: cmdBlob,
  "production-traces": cmdProductionTraces,
  instrument: cmdInstrument,
  "trace-findings": cmdTraceFindings,
  probes: cmdProbes,
};

export const DB_COMMAND_HANDLERS: Record<DbCommandName, (dbPath: string) => Promise<void>> = {
  mission: cmdMission,
  campaign: cmdCampaign,
  solve: cmdSolve,
  run: cmdRun,
  list: cmdList,
  "runtime-sessions": cmdRuntimeSessions,
  replay: cmdReplay,
  show: cmdShow,
  watch: cmdWatch,
  benchmark: cmdBenchmark,
  export: cmdExport,
  "export-training-data": cmdExportTrainingData,
  "import-package": cmdImportPackage,
  "new-scenario": cmdNewScenario,
  scenario: cmdScenario,
  tui: cmdTui,
  judge: cmdJudge,
  improve: cmdImprove,
  repl: cmdRepl,
  queue: cmdQueue,
  worker: cmdWorker,
  status: cmdStatus,
  serve: cmdServeHttp,
  "mcp-serve": cmdMcpServe,
};
