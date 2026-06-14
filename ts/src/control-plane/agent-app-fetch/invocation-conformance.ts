import type { AutoctxAgentContext } from "../../agent-runtime/index.js";
import type { AgentRuntime } from "../../runtimes/base.js";
import type { AgentAppFetchHandlerOptions } from "./index.js";
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
  const handler = await options.createHandler({
    env: {
      CONFORMANCE_ENV_VALUE: "explicit-value",
      OMITTED_ENV_VALUE: undefined,
    },
    workspaceStore: createInMemoryAgentAppFetchWorkspaceStore(),
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
}

async function assertInvokeWiresRuntime(
  options: AgentAppFetchInvocationConformanceOptions,
): Promise<void> {
  const runtime: AgentRuntime = {
    name: "conformance-runtime",
    generate: async ({ prompt }) => ({ text: `runtime:${prompt}` }),
    revise: async () => ({ text: "unused" }),
  };
  const handler = await options.createHandler({
    runtime,
    catalog: [
      {
        name: "prompted",
        relativePath: ".autoctx/agents/prompted.mjs",
        extension: ".mjs",
        handler: async (ctx: AutoctxAgentContext<Record<string, unknown>>) => {
          const agentRuntime = await ctx.init();
          const session = await agentRuntime.session("default");
          const reply = await session.prompt(readString(ctx.payload.prompt));
          return { text: reply.text, sessionId: reply.sessionId };
        },
      },
    ],
  });

  await assertJsonResponse(
    await handler(
      request("/agents/prompted/invoke", {
        method: "POST",
        headers: { "content-type": "application/json" },
        body: JSON.stringify({
          id: "runtime-run-1",
          payload: { prompt: "hello" },
        }),
      }),
    ),
    200,
    {
      ok: true,
      agent: "prompted",
      id: "runtime-run-1",
      result: {
        text: "runtime:hello",
        sessionId: "agent:prompted:default",
      },
    },
    "expected invocation to wire the explicit runtime capability",
  );
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

function assert(condition: unknown, message: string): asserts condition {
  if (!condition) throw new Error(`Agent app Fetch invocation conformance failed: ${message}`);
}
