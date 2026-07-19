/**
 * Server module — WebSocket protocol + run management (AC-347).
 */

export {
  PROTOCOL_VERSION,
  TRANSCRIPT_PROTOCOL_VERSION,
  SERVER_CAPABILITIES,
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
  MonitorAlertMsgSchema,
  PauseCmdSchema,
  ResumeCmdSchema,
  StopCmdSchema,
  InjectHintCmdSchema,
  OverrideGateCmdSchema,
  ChatAgentCmdSchema,
  StartRunCmdSchema,
  ResumeRunCmdSchema,
  ListScenariosCmdSchema,
  CreateScenarioCmdSchema,
  ConfirmScenarioCmdSchema,
  ReviseScenarioCmdSchema,
  CancelScenarioCmdSchema,
  LoginCmdSchema,
  LogoutCmdSchema,
  SwitchProviderCmdSchema,
  WhoamiCmdSchema,
  AuthStatusMsgSchema,
  ServerMessageSchema,
  ClientMessageSchema,
  parseClientMessage,
  parseServerMessage,
} from "./protocol.js";
export type { ServerMessage, ClientMessage } from "./protocol.js";

export {
  handleTuiLogin,
  handleTuiLogout,
  handleTuiSwitchProvider,
  handleTuiWhoami,
} from "./tui-auth.js";
export type { TuiLoginResult, TuiAuthStatus } from "./tui-auth.js";

export { RunManager } from "./run-manager.js";
export type { RunManagerOpts, EnvironmentInfo, RunManagerState } from "./run-manager.js";

export { InteractiveServer } from "./ws-server.js";
export type { InteractiveServerOpts } from "./ws-server.js";
