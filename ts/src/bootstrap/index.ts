/** Environment snapshot bootstrapping (AC-503). */

export type { EnvironmentSnapshot, PackageInfo } from "./snapshot.js";
export { collectSnapshot } from "./collector.js";
export {
  type RedactionConfig,
  DEFAULT_REDACTION,
  redactSnapshot,
} from "./redactor.js";
export { renderPromptSection, renderFullJson } from "./renderer.js";
