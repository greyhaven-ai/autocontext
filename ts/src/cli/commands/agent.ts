/**
 * `agent` command (AC-853 split of command-handlers.ts).
 */
import { parseArgs } from "node:util";
import { resolve } from "node:path";

async function createAutoctxAgentCliRuntime(plan: {
  provider?: string;
  model?: string;
  apiKey?: string;
  baseUrl?: string;
  env: Readonly<Record<string, string>>;
}) {
  const { loadSettings } = await import("../../config/index.js");
  const { createConfiguredProvider } = await import("../../providers/index.js");
  const { DirectAPIRuntime } = await import("../../runtimes/index.js");
  const configured = withTemporaryProcessEnv(plan.env, () =>
    createConfiguredProvider(
      {
        providerType: plan.provider,
        model: plan.model,
        apiKey: plan.apiKey,
        baseUrl: plan.baseUrl,
      },
      loadSettings(),
    ),
  );
  return {
    runtime: new DirectAPIRuntime(
      configured.provider,
      configured.config.model ?? configured.provider.defaultModel(),
    ),
    close: configured.close,
  };
}

function withTemporaryProcessEnv<T>(env: Readonly<Record<string, string>>, callback: () => T): T {
  const previous = new Map<string, string | undefined>();
  for (const [key, value] of Object.entries(env)) {
    previous.set(key, process.env[key]);
    process.env[key] = value;
  }
  try {
    return callback();
  } finally {
    for (const [key, value] of previous) {
      if (value === undefined) {
        delete process.env[key];
      } else {
        process.env[key] = value;
      }
    }
  }
}

export async function cmdAgent(): Promise<void> {
  const { values, positionals } = parseArgs({
    args: process.argv.slice(3),
    allowPositionals: true,
    options: {
      id: { type: "string" },
      payload: { type: "string" },
      env: { type: "string" },
      cwd: { type: "string" },
      json: { type: "boolean" },
      port: { type: "string" },
      host: { type: "string" },
      provider: { type: "string" },
      model: { type: "string" },
      "api-key": { type: "string" },
      "base-url": { type: "string" },
      target: { type: "string" },
      out: { type: "string" },
      help: { type: "boolean", short: "h" },
    },
  });

  const {
    AGENT_COMMAND_HELP_TEXT,
    createAutoctxAgentDevServer,
    executeAutoctxAgentBuildCommandWorkflow,
    executeAutoctxAgentRunCommandWorkflow,
    planAutoctxAgentCommand,
    renderAutoctxAgentCommandError,
  } = await import("../agent-command-workflow.js");

  if (values.help || positionals.length === 0) {
    console.log(AGENT_COMMAND_HELP_TEXT);
    process.exit(0);
  }

  let plan;
  try {
    plan = planAutoctxAgentCommand(values, positionals);
  } catch (error) {
    console.error(renderAutoctxAgentCommandError(error, !!values.json));
    process.exit(1);
  }

  if (plan.action === "run") {
    try {
      const result = await executeAutoctxAgentRunCommandWorkflow({
        plan,
        cwd: process.cwd(),
        processEnv: process.env,
        createRuntime: createAutoctxAgentCliRuntime,
      });
      if (result.stderr) process.stderr.write(result.stderr + "\n");
      if (result.stdout) process.stdout.write(result.stdout + "\n");
      process.exit(result.exitCode);
    } catch (error) {
      console.error(renderAutoctxAgentCommandError(error, plan.json));
      process.exit(1);
    }
  }

  if (plan.action === "build") {
    try {
      const result = await executeAutoctxAgentBuildCommandWorkflow({
        plan,
        cwd: process.cwd(),
      });
      if (result.stderr) process.stderr.write(result.stderr + "\n");
      if (result.stdout) process.stdout.write(result.stdout + "\n");
      process.exit(result.exitCode);
    } catch (error) {
      console.error(renderAutoctxAgentCommandError(error, plan.json));
      process.exit(1);
    }
  }

  try {
    const server = await createAutoctxAgentDevServer({
      cwd: resolve(process.cwd(), plan.cwd ?? "."),
      envPath: plan.envPath,
      processEnv: process.env,
      createRuntime: createAutoctxAgentCliRuntime,
      provider: plan.provider,
      model: plan.model,
      apiKey: plan.apiKey,
      baseUrl: plan.baseUrl,
    });
    await new Promise<void>((resolveListen, rejectListen) => {
      server.once("error", rejectListen);
      server.listen(plan.port, plan.host, () => resolveListen());
    });
    const address = server.address();
    const port = typeof address === "object" && address ? address.port : plan.port;
    const url = `http://${plan.host}:${port}`;
    if (plan.json) {
      console.log(JSON.stringify({ ok: true, url, manifest: `${url}/manifest` }, null, 2));
    } else {
      console.log(`AutoContext agent dev server listening on ${url}`);
    }
    const shutdown = () => {
      server.close(() => process.exit(0));
    };
    process.once("SIGINT", shutdown);
    process.once("SIGTERM", shutdown);
  } catch (error) {
    console.error(renderAutoctxAgentCommandError(error, plan.json));
    process.exit(1);
  }
}
