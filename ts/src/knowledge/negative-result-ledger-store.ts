import { existsSync, mkdirSync, readdirSync, readFileSync, statSync, writeFileSync } from "node:fs";
import { dirname, join } from "node:path";
import {
  negativeResultLedgerToMarkdown,
  parseNegativeResultLedger,
  type NegativeResultLedger,
} from "../analytics/negative-result-ledger.js";

export function negativeResultLedgerPath(
  knowledgeRoot: string,
  scenarioName: string,
  runId: string,
): string {
  return join(knowledgeRoot, scenarioName, "negative_result_ledgers", `${runId}.json`);
}

export function writeNegativeResultLedger(
  knowledgeRoot: string,
  scenarioName: string,
  runId: string,
  ledger: NegativeResultLedger,
): string {
  const path = negativeResultLedgerPath(knowledgeRoot, scenarioName, runId);
  mkdirSync(dirname(path), { recursive: true });
  writeFileSync(path, JSON.stringify(ledger, null, 2) + "\n", "utf-8");
  return path;
}

export function readNegativeResultLedger(
  knowledgeRoot: string,
  scenarioName: string,
  runId: string,
): NegativeResultLedger | null {
  const path = negativeResultLedgerPath(knowledgeRoot, scenarioName, runId);
  return existsSync(path)
    ? parseNegativeResultLedger(JSON.parse(readFileSync(path, "utf-8")) as unknown)
    : null;
}

export function readLatestNegativeResultLedgersMarkdown(
  knowledgeRoot: string,
  scenarioName: string,
  opts: { maxLedgers?: number } = {},
): string {
  const dir = join(knowledgeRoot, scenarioName, "negative_result_ledgers");
  if (!existsSync(dir)) return "";
  return readdirSync(dir)
    .filter((name: string) => name.endsWith(".json"))
    .map((name: string) => join(dir, name))
    .sort((left: string, right: string) => statSync(right).mtimeMs - statSync(left).mtimeMs)
    .slice(0, opts.maxLedgers ?? 2)
    .map((path: string) =>
      negativeResultLedgerToMarkdown(
        parseNegativeResultLedger(JSON.parse(readFileSync(path, "utf-8")) as unknown),
      ),
    )
    .join("\n\n");
}
