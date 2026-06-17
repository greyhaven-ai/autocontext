import { mkdtempSync, readFileSync, rmSync } from "node:fs";
import { tmpdir } from "node:os";
import { join } from "node:path";
import { describe, expect, it } from "vitest";

import {
  buildCampaignModeReport,
  parseCampaignModeReport,
  renderCampaignEvidenceShare,
  type CampaignModeReport,
  type BuildCampaignModeReportInput,
} from "../src/analytics/campaign-mode-report.js";
import {
  readCampaignModeReport,
  readLatestCampaignModeReportsMarkdown,
  writeCampaignModeReport,
} from "../src/knowledge/campaign-mode-report-store.js";

const fixture = JSON.parse(
  readFileSync(
    join(import.meta.dirname, "..", "..", "docs", "campaign-mode-report-parity-fixture.json"),
    "utf-8",
  ),
) as {
  cases: Array<
    BuildCampaignModeReportInput & {
      name: string;
      expected_report: CampaignModeReport;
    }
  >;
};

describe("campaign mode report", () => {
  it("matches the shared Python/TypeScript parity fixture", () => {
    for (const item of fixture.cases) {
      expect(buildCampaignModeReport(item)).toEqual(item.expected_report);
    }
  });

  it("parses durable report JSON and rejects schema-invalid data", () => {
    const report = fixture.cases[1]!.expected_report;
    const branch = report.branches[0]!;
    const { budget: _budget, ...missingBudget } = branch;

    expect(parseCampaignModeReport(report)).toEqual(report);
    expect(() => parseCampaignModeReport({ ...report, surprise: true })).toThrow(/unexpected field/);
    expect(() => parseCampaignModeReport({ ...report, campaign_id: "" })).toThrow(/campaign_id/);
    expect(() =>
      parseCampaignModeReport({ ...report, branches: [{ ...branch, terminal_state: "unknown" }] }),
    ).toThrow(/terminal_state/);
    expect(() => parseCampaignModeReport({ ...report, branches: [missingBudget] })).toThrow(
      /missing field/,
    );
    expect(() =>
      parseCampaignModeReport({
        ...report,
        branch_budget_defaults: { ...report.branch_budget_defaults, max_tokens: -1 },
      }),
    ).toThrow(/max_tokens/);
  });

  it("renders only budget-included shared evidence", () => {
    const report = parseCampaignModeReport(fixture.cases[1]!.expected_report);

    const rendered = renderCampaignEvidenceShare(report);

    expect(rendered).toContain("share-safe-1");
    expect(rendered).not.toContain("share-risky-1");
    expect(rendered).toContain("Safe branch passed both eval lanes");
  });

  it("persists campaign mode reports through file helpers", () => {
    const root = mkdtempSync(join(tmpdir(), "campaign-mode-"));
    try {
      const knowledgeRoot = join(root, "knowledge");
      const report = parseCampaignModeReport(fixture.cases[1]!.expected_report);

      writeCampaignModeReport(knowledgeRoot, "grid_ctf", report.run_id, report);

      expect(readCampaignModeReport(knowledgeRoot, "grid_ctf", report.run_id)).toEqual(report);
      expect(readLatestCampaignModeReportsMarkdown(knowledgeRoot, "grid_ctf")).toContain(
        "Campaign Mode Report",
      );
    } finally {
      rmSync(root, { recursive: true, force: true });
    }
  });
});
