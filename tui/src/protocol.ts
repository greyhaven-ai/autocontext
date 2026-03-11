// Re-export all protocol types from the auto-generated file.
// The single source of truth is mts/src/mts/server/protocol.py.
// Run `python scripts/generate_protocol.py` to regenerate.
export {
  // Shared models
  ExecutorResourcesSchema,
  ExecutorInfoSchema,
  ScenarioInfoSchema,
  ScoringComponentSchema,
  StrategyParamSchema,
  // Server messages
  ServerMessageSchema,
  HelloMsgSchema,
  EventMsgSchema,
  StateMsgSchema,
  ChatResponseMsgSchema,
  EnvironmentsMsgSchema,
  RunAcceptedMsgSchema,
  AckMsgSchema,
  ErrorMsgSchema,
  ScenarioGeneratingMsgSchema,
  ScenarioPreviewMsgSchema,
  ScenarioReadyMsgSchema,
  ScenarioErrorMsgSchema,
  // Client messages
  ClientMessageSchema,
  PauseCmdSchema,
  ResumeCmdSchema,
  InjectHintCmdSchema,
  OverrideGateCmdSchema,
  ChatAgentCmdSchema,
  StartRunCmdSchema,
  ListScenariosCmdSchema,
  CreateScenarioCmdSchema,
  ConfirmScenarioCmdSchema,
  ReviseScenarioCmdSchema,
  CancelScenarioCmdSchema,
  // Utility
  parseServerMessage,
} from "./protocol.generated.js";
