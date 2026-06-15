import type { AutoctxAgentContext } from "../../agent-runtime/index.js";
import type { AgentRuntime } from "../../runtimes/base.js";
import type { AgentAppFetchHandlerOptions } from "./index.js";
import type { AgentAppFetchRuntimeFactory } from "./runtime-factory.js";
import { planAgentAppFetchRuntimeFactories } from "./runtime-factory.js";
import type { AgentAppFetchWorkspaceStore } from "./workspace-store.js";
import { createInMemoryAgentAppFetchWorkspaceStore } from "./workspace-store.js";

export type AgentAppFetchInvocationConformanceHandler = (
  request: Request,
) => MaybePromise<Response>;

export interface AgentAppFetchInvocationConformanceCase {
  name: string;
  run(): Promise<void>;
}

export interface AgentAppFetchInvocationConformanceOptions {
  createHandler(
    options: AgentAppFetchHandlerOptions<Record<string, unknown>, unknown>,
  ): MaybePromise<AgentAppFetchInvocationConformanceHandler>;
}

type MaybePromise<T> = T | Promise<T>;

export function createAgentAppFetchInvocationConformanceCases(
  options: AgentAppFetchInvocationConformanceOptions,
): AgentAppFetchInvocationConformanceCase[] {
  return [
    {
      name: "Fetch invocation manifests advertise agents without loading handlers",
      run: () => assertManifestRoutesDoNotLoadHandlers(options),
    },
    {
      name: "Fetch invocation posts payloads with explicit env and workspace",
      run: () => assertInvokeWiresEnvAndWorkspace(options),
    },
    {
      name: "Fetch invocation wires explicit runtime capability",
      run: () => assertInvokeWiresRuntime(options),
    },
    {
      name: "Fetch invocation wires explicit runtime factory capability",
      run: () => assertInvokeWiresRuntimeFactory(options),
    },
    {
      name: "Fetch invocation prefers explicit runtime over runtime factories",
      run: () => assertRuntimePrecedenceOverRuntimeFactory(options),
    },
    {
      name: "Fetch invocation prefers explicit runtime factory over named runtime factories",
      run: () => assertRuntimeFactoryPrecedenceOverRuntimeFactoryName(options),
    },
    {
      name: "Fetch invocation selects named runtime factories lazily",
      run: () => assertNamedRuntimeFactoryLaziness(options),
    },
    {
      name: "Fetch invocation returns stable error envelopes",
      run: () => assertStableErrorEnvelopes(options),
    },
  ];
}

export async function runAgentAppFetchInvocationConformance(
  options: AgentAppFetchInvocationConformanceOptions,
): Promise<void> {
  for (const testCase of createAgentAppFetchInvocationConformanceCases(options)) {
    await testCase.run();
  }
}

async function assertManifestRoutesDoNotLoadHandlers(
  options: AgentAppFetchInvocationConformanceOptions,
): Promise<void> {
  let loadCalls = 0;
  const handler = await options.createHandler({
    catalog: [
      {
        name: "support",
        relativePath: ".autoctx/agents/support.mjs",
        extension: ".mjs",
        triggers: { webhook: true },
        load: async () => {
          loadCalls += 1;
          return {
            name: "support",
            relativePath: ".autoctx/agents/support.mjs",
            handler: async () => ({ ok: true }),
          };
        },
      },
    ],
  });
  const expectedManifest = {
    ok: true,
    target: "fetch",
    agents: [
      {
        name: "support",
        relativePath: ".autoctx/agents/support.mjs",
        extension: ".mjs",
        triggers: { webhook: true },
      },
    ],
  };

  await assertJsonResponse(
    await handler(request("/manifest")),
    200,
    expectedManifest,
    "expected GET /manifest to return the static catalog manifest",
  );
  await assertJsonResponse(
    await handler(request("/agents")),
    200,
    expectedManifest,
    "expected GET /agents to return the static catalog manifest alias",
  );
  assert(loadCalls === 0, "expected manifest routes not to load handler modules");
}

async function assertInvokeWiresEnvAndWorkspace(
  options: AgentAppFetchInvocationConformanceOptions,
): Promise<void> {
  const workspaceStore = createRecordingWorkspaceStore();
  const handler = await options.createHandler({
    env: {
      CONFORMANCE_ENV_VALUE: "explicit-value",
      OMITTED_ENV_VALUE: undefined,
    },
    workspaceStore,
    catalog: [
      {
        name: "support",
        relativePath: ".autoctx/agents/support.mjs",
        extension: ".mjs",
        triggers: { webhook: true },
        handler: async (ctx: AutoctxAgentContext<Record<string, unknown>>) => {
          const message = readString(ctx.payload.message);
          await ctx.workspace.writeFile("scratch/result.txt", message);
          return {
            id: ctx.id,
            message: await ctx.workspace.readFile("scratch/result.txt"),
            cwd: ctx.workspace.cwd,
            envValue: ctx.env.CONFORMANCE_ENV_VALUE,
            omitted: ctx.env.OMITTED_ENV_VALUE,
          };
        },
      },
    ],
  });

  await assertJsonResponse(
    await handler(
      request("/agents/support/invoke", {
        method: "POST",
        headers: { "content-type": "application/json" },
        body: JSON.stringify({
          id: "conformance-run-1",
          payload: { message: "please triage" },
        }),
      }),
    ),
    200,
    {
      ok: true,
      agent: "support",
      id: "conformance-run-1",
      result: {
        id: "conformance-run-1",
        message: "please triage",
        cwd: "/",
        envValue: "explicit-value",
      },
    },
    "expected POST /agents/:agent/invoke to wire payload, env, and workspace",
  );
  assert(
    workspaceStore.calls.includes("writeFile:/scratch/result.txt") &&
      workspaceStore.calls.includes("readFile:/scratch/result.txt"),
    "expected supplied workspaceStore to be used",
  );
}

async function assertInvokeWiresRuntime(
  options: AgentAppFetchInvocationConformanceOptions,
): Promise<void> {
  const runtime = createConformanceRuntime("runtime");
  const handler = await options.createHandler({
    runtime,
    catalog: createRuntimePromptCatalog(),
  });

  await assertPromptResponse(
    handler,
    "runtime-run-1",
    "runtime:hello",
    "runtime-runtime",
    "expected invocation to wire the explicit runtime capability",
  );
}

async function assertInvokeWiresRuntimeFactory(
  options: AgentAppFetchInvocationConformanceOptions,
): Promise<void> {
  const calls: { factory: number } = { factory: 0 };
  const runtimeFactory: AgentAppFetchRuntimeFactory = () => {
    calls.factory += 1;
    return createConformanceRuntime("factory");
  };
  const handler = await options.createHandler({
    runtimeFactory,
    catalog: createRuntimePromptCatalog(),
  });

  assert(calls.factory === 0, "expected runtimeFactory not to run at handler creation");
  await assertPromptResponse(
    handler,
    "runtime-factory-run-1",
    "factory:hello",
    "factory-runtime",
    "expected explicit runtimeFactory to be used",
  );
  assertCount(calls.factory, 1, "expected runtimeFactory to load lazily during invocation");
}

async function assertRuntimePrecedenceOverRuntimeFactory(
  options: AgentAppFetchInvocationConformanceOptions,
): Promise<void> {
  const calls: { factory: number } = { factory: 0 };
  const runtimeFactory: AgentAppFetchRuntimeFactory = () => {
    calls.factory += 1;
    return createConformanceRuntime("factory");
  };
  const handler = await options.createHandler({
    runtime: createConformanceRuntime("runtime"),
    runtimeFactory,
    catalog: createRuntimePromptCatalog(),
  });

  await assertPromptResponse(
    handler,
    "runtime-precedence-run-1",
    "runtime:hello",
    "runtime-runtime",
    "expected explicit runtime to take precedence over runtimeFactory",
  );
  assert(calls.factory === 0, "expected runtimeFactory not to run when runtime is provided");
}

async function assertRuntimeFactoryPrecedenceOverRuntimeFactoryName(
  options: AgentAppFetchInvocationConformanceOptions,
): Promise<void> {
  const calls: { factory: number; namedRuntimeLoads: number } = {
    factory: 0,
    namedRuntimeLoads: 0,
  };
  const runtimeFactory: AgentAppFetchRuntimeFactory = () => {
    calls.factory += 1;
    return createConformanceRuntime("factory");
  };
  const handler = await options.createHandler({
    runtimeFactory,
    runtimeFactoryName: "named",
    runtimeFactoryPlan: createNamedRuntimeFactoryPlan(),
    runtimeFactoryModuleMap: {
      named: () => {
        calls.namedRuntimeLoads += 1;
        return { default: () => createConformanceRuntime("named") };
      },
    },
    catalog: createRuntimePromptCatalog(),
  });

  await assertPromptResponse(
    handler,
    "runtime-factory-precedence-run-1",
    "factory:hello",
    "factory-runtime",
    "expected explicit runtimeFactory to take precedence over runtimeFactoryName",
  );
  assertCount(calls.factory, 1, "expected explicit runtimeFactory to load during invocation");
  assert(calls.namedRuntimeLoads === 0, "expected named runtimeFactory not to load");
}

async function assertNamedRuntimeFactoryLaziness(
  options: AgentAppFetchInvocationConformanceOptions,
): Promise<void> {
  const calls: { namedRuntimeLoads: number; namedFactoryCalls: number } = {
    namedRuntimeLoads: 0,
    namedFactoryCalls: 0,
  };
  const handler = await options.createHandler({
    runtimeFactoryName: "named",
    runtimeFactoryPlan: createNamedRuntimeFactoryPlan(),
    runtimeFactoryModuleMap: {
      named: () => {
        calls.namedRuntimeLoads += 1;
        return {
          default: () => {
            calls.namedFactoryCalls += 1;
            return createConformanceRuntime("named");
          },
        };
      },
    },
    catalog: createRuntimePromptCatalog(),
  });

  assert(
    calls.namedRuntimeLoads === 0,
    "expected named runtimeFactory module not to load at handler creation",
  );
  assert(
    calls.namedFactoryCalls === 0,
    "expected named runtimeFactory not to run at handler creation",
  );
  await assertPromptResponse(
    handler,
    "named-runtime-factory-run-1",
    "named:hello",
    "named-runtime",
    "expected runtimeFactoryName to select a bundled runtime factory lazily",
  );
  assertCount(calls.namedRuntimeLoads, 1, "expected named runtimeFactory module to load once");
  assertCount(calls.namedFactoryCalls, 1, "expected named runtimeFactory to run once");
}

async function assertStableErrorEnvelopes(
  options: AgentAppFetchInvocationConformanceOptions,
): Promise<void> {
  const handler = await options.createHandler({
    catalog: [
      {
        name: "support",
        relativePath: ".autoctx/agents/support.mjs",
        extension: ".mjs",
        handler: async () => {
          throw new Error("handler exploded");
        },
      },
    ],
  });
  const tooLargeHandler = await options.createHandler({
    maxBodyBytes: 1,
    catalog: [
      {
        name: "support",
        relativePath: ".autoctx/agents/support.mjs",
        extension: ".mjs",
        handler: async () => ({ ok: true }),
      },
    ],
  });

  await assertJsonResponse(
    await handler(request("/agents/missing/invoke", { method: "POST" })),
    404,
    {
      ok: false,
      error: {
        code: "AUTOCTX_AGENT_NOT_FOUND",
        message: "AutoContext agent not found: missing. Available: support",
      },
    },
    "expected missing agents to return a stable 404 envelope",
  );

  const invalidJson = await parseJsonResponse(
    await handler(
      request("/agents/support/invoke", {
        method: "POST",
        headers: { "content-type": "application/json" },
        body: "{not-json",
      }),
    ),
    400,
    "expected invalid JSON to return a stable bad-request envelope",
  );
  assertDeepEqual(
    readErrorCodeEnvelope(invalidJson),
    { ok: false, error: { code: "AUTOCTX_AGENT_APP_BAD_REQUEST" } },
    "expected invalid JSON envelope code",
  );

  await assertJsonResponse(
    await tooLargeHandler(
      request("/agents/support/invoke", {
        method: "POST",
        headers: {
          "content-type": "application/json",
          "content-length": "2",
        },
        body: "{}",
      }),
    ),
    413,
    {
      ok: false,
      error: {
        code: "AUTOCTX_AGENT_APP_REQUEST_TOO_LARGE",
        message: "Request body is too large",
      },
    },
    "expected body limit errors to return a stable 413 envelope",
  );

  await assertJsonResponse(
    await handler(
      request("/agents/support/invoke", {
        method: "POST",
        headers: { "content-type": "application/json" },
        body: JSON.stringify({ payload: {} }),
      }),
    ),
    500,
    {
      ok: false,
      error: {
        code: "AUTOCTX_AGENT_APP_ERROR",
        message: "handler exploded",
      },
    },
    "expected handler failures to return a stable 500 envelope",
  );
}

async function assertPromptResponse(
  handler: AgentAppFetchInvocationConformanceHandler,
  id: string,
  expectedText: string,
  expectedRuntimeMetadata: string,
  message: string,
): Promise<void> {
  await assertJsonResponse(
    await handler(
      request("/agents/prompted/invoke", {
        method: "POST",
        headers: { "content-type": "application/json" },
        body: JSON.stringify({
          id,
          payload: { prompt: "hello" },
        }),
      }),
    ),
    200,
    {
      ok: true,
      agent: "prompted",
      id,
      result: {
        text: expectedText,
        sessionId: "agent:prompted:default",
        runtimeMetadata: expectedRuntimeMetadata,
      },
    },
    message,
  );
}

function createRuntimePromptCatalog(): AgentAppFetchHandlerOptions<
  Record<string, unknown>,
  unknown
>["catalog"] {
  return [
    {
      name: "prompted",
      relativePath: ".autoctx/agents/prompted.mjs",
      extension: ".mjs",
      handler: async (ctx: AutoctxAgentContext<Record<string, unknown>>) => {
        const agentRuntime = await ctx.init();
        const session = await agentRuntime.session("default");
        const reply = await session.prompt(readString(ctx.payload.prompt));
        return {
          text: reply.text,
          sessionId: reply.sessionId,
          runtimeMetadata: readAssistantRuntimeMetadata(reply.sessionLog.events),
        };
      },
    },
  ];
}

function createConformanceRuntime(name: string): AgentRuntime {
  return {
    name: `${name}-runtime`,
    generate: async ({ prompt }) => ({ text: `${name}:${prompt}` }),
    revise: async ({ prompt }) => ({ text: `${name}:revise:${prompt}` }),
  };
}

function createNamedRuntimeFactoryPlan() {
  return planAgentAppFetchRuntimeFactories({
    entries: [
      {
        name: "named",
        relativePath: ".autoctx/runtimes/named.mjs",
        extension: ".mjs",
      },
    ],
  });
}

function readAssistantRuntimeMetadata(
  events: readonly { eventType: string; payload: Record<string, unknown> }[],
): unknown {
  const assistantEvent = [...events]
    .reverse()
    .find((event) => event.eventType === "assistant_message");
  const metadata = isRecord(assistantEvent?.payload.metadata)
    ? assistantEvent.payload.metadata
    : {};
  return metadata.runtime;
}

async function assertJsonResponse(
  response: Response,
  expectedStatus: number,
  expectedBody: unknown,
  message: string,
): Promise<void> {
  const body = await parseJsonResponse(response, expectedStatus, message);
  assertDeepEqual(body, expectedBody, message);
}

async function parseJsonResponse(
  response: Response,
  expectedStatus: number,
  message: string,
): Promise<unknown> {
  assert(response.status === expectedStatus, `${message}: expected HTTP ${expectedStatus}`);
  const contentType = response.headers.get("content-type") ?? "";
  assert(
    contentType.includes("application/json"),
    `${message}: expected JSON content-type, received ${contentType}`,
  );
  return JSON.parse(await response.text()) as unknown;
}

function readErrorCodeEnvelope(body: unknown): unknown {
  if (!isRecord(body)) return body;
  const error = isRecord(body.error) ? body.error : {};
  return {
    ok: body.ok,
    error: { code: error.code },
  };
}

function createRecordingWorkspaceStore(): AgentAppFetchWorkspaceStore & {
  readonly calls: string[];
} {
  const delegate = createInMemoryAgentAppFetchWorkspaceStore();
  const calls: string[] = [];
  return {
    capabilities: delegate.capabilities,
    calls,
    async readFile(path) {
      calls.push(`readFile:${path}`);
      return await delegate.readFile(path);
    },
    async writeFile(path, content) {
      calls.push(`writeFile:${path}`);
      await delegate.writeFile(path, content);
    },
    async stat(path) {
      calls.push(`stat:${path}`);
      return await delegate.stat(path);
    },
    async readdir(path) {
      calls.push(`readdir:${path}`);
      return await delegate.readdir(path);
    },
    async exists(path) {
      calls.push(`exists:${path}`);
      return await delegate.exists(path);
    },
    async mkdir(path, options) {
      calls.push(`mkdir:${path}`);
      await delegate.mkdir(path, options);
    },
    async rm(path, options) {
      calls.push(`rm:${path}`);
      await delegate.rm(path, options);
    },
  };
}

function request(path: string, init?: RequestInit): Request {
  return new Request(`https://agent-app-conformance.test${path}`, init);
}

function readString(value: unknown): string {
  return typeof value === "string" ? value : "";
}

function isRecord(value: unknown): value is Record<string, unknown> {
  return typeof value === "object" && value !== null && !Array.isArray(value);
}

function assertDeepEqual(actual: unknown, expected: unknown, message: string): void {
  const renderedActual = JSON.stringify(actual);
  const renderedExpected = JSON.stringify(expected);
  assert(
    renderedActual === renderedExpected,
    `${message}: expected ${renderedExpected}, received ${renderedActual}`,
  );
}

function assertCount(actual: number, expected: number, message: string): void {
  assert(actual === expected, `${message}: expected ${expected}, received ${actual}`);
}

function assert(condition: unknown, message: string): asserts condition {
  if (!condition) throw new Error(`Agent app Fetch invocation conformance failed: ${message}`);
}
