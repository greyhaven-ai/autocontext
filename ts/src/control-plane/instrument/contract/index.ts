/**
 * Public barrel for the A2-I instrument/contract module.
 *
 * Only this file may re-export types to sibling instrument sub-contexts.
 */
export type {
  InstrumentLanguage,
  DirectiveMap,
  DirectiveValue,
  IndentationStyle,
  ExistingImport,
  ImportSet,
  SourceRange,
  ImportSpec,
  BaseEdit,
  WrapExpressionEdit,
  InsertStatementEdit,
  ReplaceExpressionEdit,
  EditDescriptor,
  SecretMatch,
  SourceFile,
  DetectorPlugin,
  TreeSitterMatch,
  InstrumentSession,
  InstrumentFlagsSnapshot,
  PlanSourceFileMetadata,
  ConflictDecision,
  SafetyDecision,
  InstrumentPlan,
} from "./plugin-interface.js";

export {
  validateInstrumentSession,
  validateInstrumentPlan,
  type ValidationResult,
} from "./validators.js";
