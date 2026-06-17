import { existsSync, mkdirSync, readdirSync, readFileSync, statSync, writeFileSync } from "node:fs";
import { dirname, join } from "node:path";
import {
  campaignModeReportToMarkdown,
  parseCampaignModeReport,
  type CampaignModeReport,
} from "../analytics/campaign-mode-report.js";

export function campaignModeReportPath(
  knowledgeRoot: string,
  scenarioName: string,
  runId: string,
): string {
  return join(knowledgeRoot, scenarioName, "campaign_mode_reports", `${runId}.json`);
}

export function writeCampaignModeReport(
  knowledgeRoot: string,
  scenarioName: string,
  runId: string,
  report: CampaignModeReport,
): string {
  const path = campaignModeReportPath(knowledgeRoot, scenarioName, runId);
  mkdirSync(dirname(path), { recursive: true });
  writeFileSync(path, JSON.stringify(report, null, 2) + "\n", "utf-8");
  return path;
}

export function readCampaignModeReport(
  knowledgeRoot: string,
  scenarioName: string,
  runId: string,
): CampaignModeReport | null {
  const path = campaignModeReportPath(knowledgeRoot, scenarioName, runId);
  return existsSync(path)
    ? parseCampaignModeReport(JSON.parse(readFileSync(path, "utf-8")) as unknown)
    : null;
}

export function readLatestCampaignModeReportsMarkdown(
  knowledgeRoot: string,
  scenarioName: string,
  opts: { maxReports?: number } = {},
): string {
  const dir = join(knowledgeRoot, scenarioName, "campaign_mode_reports");
  if (!existsSync(dir)) return "";
  return readdirSync(dir)
    .filter((name: string) => name.endsWith(".json"))
    .map((name: string) => join(dir, name))
    .sort((left: string, right: string) => statSync(right).mtimeMs - statSync(left).mtimeMs)
    .slice(0, opts.maxReports ?? 2)
    .map((path: string) =>
      campaignModeReportToMarkdown(
        parseCampaignModeReport(JSON.parse(readFileSync(path, "utf-8")) as unknown),
      ),
    )
    .join("\n\n");
}
