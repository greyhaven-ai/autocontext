/** Browsable prior-run evidence workspace (AC-504). */

export {
  type EvidenceArtifact,
  type EvidenceWorkspace,
  getArtifact,
  listByKind,
} from "./workspace.js";
export {
  materializeWorkspace,
  scanRunArtifacts,
  scanKnowledgeArtifacts,
} from "./materializer.js";
export { renderEvidenceManifest, renderArtifactDetail } from "./manifest.js";
export {
  recordAccess,
  saveAccessLog,
  loadAccessLog,
  computeUtilization,
} from "./tracker.js";
