import {
  createAgentAppFetchHandler,
  createAgentAppFetchInvocationConformanceCases,
  createAgentAppFetchSessionEventStoreConformanceCases,
  createAgentAppFetchWorkspaceStoreConformanceCases,
  createInMemoryAgentAppFetchSessionEventStore,
  createInMemoryAgentAppFetchWorkspaceStore,
  runAgentAppFetchInvocationConformance,
  runAgentAppFetchSessionEventStoreConformance,
  runAgentAppFetchWorkspaceStoreConformance,
  type AgentAppFetchHandlerOptions,
  type AgentAppFetchInvocationConformanceCase,
  type AgentAppFetchInvocationConformanceHandler,
  type AgentAppFetchSessionEventStore,
  type AgentAppFetchStoreConformanceCase,
  type AgentAppFetchWorkspaceStore,
} from "../src/control-plane/agent-app-fetch/index.js";

export interface FetchConformanceHostWrapperExampleOptions {
  createWorkspaceStore?: () => MaybePromise<AgentAppFetchWorkspaceStore>;
  createSessionEventStore?: () => MaybePromise<AgentAppFetchSessionEventStore>;
  createHandler?: (
    options: AgentAppFetchHandlerOptions<Record<string, unknown>, unknown>,
  ) => MaybePromise<AgentAppFetchInvocationConformanceHandler>;
}

export interface FetchConformanceHostWrapperExample {
  createWorkspaceStore(): MaybePromise<AgentAppFetchWorkspaceStore>;
  createSessionEventStore(): MaybePromise<AgentAppFetchSessionEventStore>;
  createHandler(
    options: AgentAppFetchHandlerOptions<Record<string, unknown>, unknown>,
  ): MaybePromise<AgentAppFetchInvocationConformanceHandler>;
  workspaceStoreCases: AgentAppFetchStoreConformanceCase[];
  sessionEventStoreCases: AgentAppFetchStoreConformanceCase[];
  invocationCases: AgentAppFetchInvocationConformanceCase[];
  runWorkspaceStoreConformance(): Promise<void>;
  runSessionEventStoreConformance(): Promise<void>;
  runInvocationConformance(): Promise<void>;
  runAllConformance(): Promise<void>;
}

type MaybePromise<T> = T | Promise<T>;

export function buildFetchConformanceHostWrapperExample(
  options: FetchConformanceHostWrapperExampleOptions = {},
): FetchConformanceHostWrapperExample {
  const createWorkspaceStore =
    options.createWorkspaceStore ?? (() => createInMemoryAgentAppFetchWorkspaceStore());
  const createSessionEventStore =
    options.createSessionEventStore ?? (() => createInMemoryAgentAppFetchSessionEventStore());
  const createHandler = options.createHandler ?? createDefaultHostFetchHandler;
  const workspaceStoreOptions = { createStore: createWorkspaceStore };
  const sessionEventStoreOptions = { createStore: createSessionEventStore };
  const invocationOptions = { createHandler };

  return {
    createWorkspaceStore,
    createSessionEventStore,
    createHandler,
    workspaceStoreCases: createAgentAppFetchWorkspaceStoreConformanceCases(
      workspaceStoreOptions,
    ),
    sessionEventStoreCases: createAgentAppFetchSessionEventStoreConformanceCases(
      sessionEventStoreOptions,
    ),
    invocationCases: createAgentAppFetchInvocationConformanceCases(invocationOptions),
    async runWorkspaceStoreConformance() {
      await runAgentAppFetchWorkspaceStoreConformance(workspaceStoreOptions);
    },
    async runSessionEventStoreConformance() {
      await runAgentAppFetchSessionEventStoreConformance(sessionEventStoreOptions);
    },
    async runInvocationConformance() {
      await runAgentAppFetchInvocationConformance(invocationOptions);
    },
    async runAllConformance() {
      await runAgentAppFetchWorkspaceStoreConformance(workspaceStoreOptions);
      await runAgentAppFetchSessionEventStoreConformance(sessionEventStoreOptions);
      await runAgentAppFetchInvocationConformance(invocationOptions);
    },
  };
}

function createDefaultHostFetchHandler(
  options: AgentAppFetchHandlerOptions<Record<string, unknown>, unknown>,
): AgentAppFetchInvocationConformanceHandler {
  // Conformance cases intentionally issue unsigned synthetic requests. This
  // escape hatch belongs only in this isolated test harness, never a host.
  return createAgentAppFetchHandler({
    ...options,
    allowInsecureUnauthenticated: true,
  });
}
