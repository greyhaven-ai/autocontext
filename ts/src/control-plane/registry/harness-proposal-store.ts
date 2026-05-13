import {
  existsSync,
  mkdirSync,
  readFileSync,
  readdirSync,
  renameSync,
  statSync,
  writeFileSync,
} from "node:fs";
import { join } from "node:path";
import { parseHarnessProposalId, type HarnessProposalId } from "../contract/branded-ids.js";
import type { HarnessChangeProposal } from "../contract/types.js";
import { canonicalJsonStringify } from "../contract/canonical-json.js";
import { validateHarnessChangeProposal } from "../contract/validators.js";

const ROOT = ".autocontext";
const HARNESS_PROPOSALS = "harness-proposals";

function proposalDir(registryRoot: string): string {
  return join(registryRoot, ROOT, HARNESS_PROPOSALS);
}

function proposalPath(registryRoot: string, id: HarnessProposalId): string {
  return join(proposalDir(registryRoot), `${id}.json`);
}

export function saveHarnessChangeProposal(
  registryRoot: string,
  proposal: HarnessChangeProposal,
): void {
  const validation = validateHarnessChangeProposal(proposal);
  if (!validation.valid) {
    throw new Error(`saveHarnessChangeProposal: invalid HarnessChangeProposal: ${validation.errors.join("; ")}`);
  }
  const path = proposalPath(registryRoot, proposal.id);
  if (existsSync(path)) {
    throw new Error(`saveHarnessChangeProposal: proposal already exists at ${path}`);
  }
  mkdirSync(proposalDir(registryRoot), { recursive: true });
  writeFileSync(path, canonicalJsonStringify(proposal), "utf-8");
}

export function updateHarnessChangeProposal(
  registryRoot: string,
  proposal: HarnessChangeProposal,
): void {
  const validation = validateHarnessChangeProposal(proposal);
  if (!validation.valid) {
    throw new Error(`updateHarnessChangeProposal: invalid HarnessChangeProposal: ${validation.errors.join("; ")}`);
  }
  const path = proposalPath(registryRoot, proposal.id);
  if (!existsSync(path)) {
    throw new Error(`updateHarnessChangeProposal: proposal ${proposal.id} not found at ${path}`);
  }
  const tmp = `${path}.tmp`;
  writeFileSync(tmp, canonicalJsonStringify(proposal), "utf-8");
  renameSync(tmp, path);
}

export function loadHarnessChangeProposal(
  registryRoot: string,
  id: HarnessProposalId,
): HarnessChangeProposal {
  const path = proposalPath(registryRoot, id);
  if (!existsSync(path)) {
    throw new Error(`loadHarnessChangeProposal: proposal ${id} not found at ${path}`);
  }
  let parsed: unknown;
  try {
    parsed = JSON.parse(readFileSync(path, "utf-8"));
  } catch (err) {
    const message = err instanceof Error ? err.message : String(err);
    throw new Error(`loadHarnessChangeProposal: ${path} is not valid JSON: ${message}`);
  }
  const validation = validateHarnessChangeProposal(parsed);
  if (!validation.valid) {
    throw new Error(`loadHarnessChangeProposal: stored HarnessChangeProposal failed validation: ${validation.errors.join("; ")}`);
  }
  if (isHarnessChangeProposal(parsed)) {
    return parsed;
  }
  throw new Error("loadHarnessChangeProposal: stored HarnessChangeProposal failed validation");
}

export function listHarnessChangeProposalIds(registryRoot: string): HarnessProposalId[] {
  const dir = proposalDir(registryRoot);
  if (!existsSync(dir)) return [];
  const ids: HarnessProposalId[] = [];
  for (const entry of readdirSync(dir)) {
    if (!entry.endsWith(".json")) continue;
    const full = join(dir, entry);
    try {
      if (statSync(full).isFile()) {
        const id = parseHarnessProposalId(entry.slice(0, -".json".length));
        if (id !== null) ids.push(id);
      }
    } catch {
      // ignore unreadable entries
    }
  }
  return ids;
}

function isHarnessChangeProposal(input: unknown): input is HarnessChangeProposal {
  return validateHarnessChangeProposal(input).valid;
}
