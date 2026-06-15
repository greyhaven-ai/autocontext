import { createAgentAppFetchHostCapabilityManifest } from "./capability-manifest.js";
import type { AgentAppFetchCatalogPlan } from "./catalog-planner.js";
import type { AgentAppFetchRuntimeFactoryPlan } from "./runtime-factory.js";

export interface RenderAgentAppFetchEntrypointTemplateOptions {
  packageSpecifier?: string;
  runtimeFactoryPlan?: AgentAppFetchRuntimeFactoryPlan;
}

const DEFAULT_AGENT_APP_FETCH_PACKAGE_SPECIFIER = "autoctx/control-plane/agent-app-fetch";

export function renderAgentAppFetchEntrypointTemplate(
  plan: AgentAppFetchCatalogPlan,
  options: RenderAgentAppFetchEntrypointTemplateOptions = {},
): string {
  const packageSpecifier = options.packageSpecifier ?? DEFAULT_AGENT_APP_FETCH_PACKAGE_SPECIFIER;
  const runtimeFactoryPlan = options.runtimeFactoryPlan;
  const hostCapabilityManifest = createAgentAppFetchHostCapabilityManifest(plan);
  const renderedHostCapabilityManifest = JSON.stringify(hostCapabilityManifest, null, 2);
  const moduleMapEntries = plan.entries.map(
    (entry) =>
      `  ${renderObjectKey(entry.name)}: () => import(${JSON.stringify(entry.importSpecifier)}),`,
  );
  const runtimeFactoryModuleMapEntries = runtimeFactoryPlan?.entries.map(
    (entry) =>
      `  ${renderObjectKey(entry.name)}: () => import(${JSON.stringify(entry.importSpecifier)}),`,
  );
  const imports = [
    "createAgentAppFetchCatalogFromModuleMap",
    "createAgentAppFetchHandler",
    "createAgentAppFetchLazyRuntime",
    ...(runtimeFactoryPlan ? ["createAgentAppFetchRuntimeFactoryFromModuleMap"] : []),
  ].join(", ");
  const runtimeFactoryExports = runtimeFactoryPlan
    ? [
        `export const agentAppFetchRuntimeFactoryPlan = ${JSON.stringify(runtimeFactoryPlan, null, 2)};`,
        "",
        "export const agentAppFetchRuntimeFactoryModuleMap = {",
        ...(runtimeFactoryModuleMapEntries ?? []),
        "};",
        "",
      ]
    : [];
  const bundledRuntimeFactoryLines = runtimeFactoryPlan
    ? [
        "  const bundledRuntimeFactory = !hostCapabilities.runtime &&",
        "    !hostCapabilities.runtimeFactory &&",
        "    hostCapabilities.runtimeFactoryName",
        "    ? createAgentAppFetchRuntimeFactoryFromModuleMap(",
        "        agentAppFetchRuntimeFactoryPlan,",
        "        agentAppFetchRuntimeFactoryModuleMap,",
        "        hostCapabilities.runtimeFactoryName,",
        "      )",
        "    : undefined;",
      ]
    : ["  const bundledRuntimeFactory = undefined;"];
  return [
    `import { ${imports} } from ${JSON.stringify(packageSpecifier)};`,
    "",
    `export const agentAppFetchCatalogPlan = ${JSON.stringify(plan, null, 2)};`,
    "",
    `export const agentAppFetchHostCapabilityManifest = ${renderedHostCapabilityManifest};`,
    "",
    "export const agentAppFetchModuleMap = {",
    ...moduleMapEntries,
    "};",
    "",
    "export const agentAppFetchCatalog = createAgentAppFetchCatalogFromModuleMap(",
    "  agentAppFetchCatalogPlan,",
    "  agentAppFetchModuleMap,",
    ");",
    "",
    ...runtimeFactoryExports,
    "export function createAgentAppFetchEntrypoint(hostCapabilities = {}) {",
    ...bundledRuntimeFactoryLines,
    "  const runtimeFactory = hostCapabilities.runtimeFactory ?? bundledRuntimeFactory;",
    "  return createAgentAppFetchHandler({",
    "    catalog: agentAppFetchCatalog,",
    "    env: hostCapabilities.env,",
    "    workspace: hostCapabilities.workspace,",
    "    workspaceStore: hostCapabilities.workspaceStore,",
    "    runtime: hostCapabilities.runtime ??",
    "      (runtimeFactory ? createAgentAppFetchLazyRuntime(runtimeFactory) : undefined),",
    "    commands: hostCapabilities.commands,",
    "    tools: hostCapabilities.tools,",
    "    eventStore: hostCapabilities.eventStore,",
    "    sessionEventStore: hostCapabilities.sessionEventStore,",
    "    eventSink: hostCapabilities.eventSink,",
    "    maxBodyBytes: hostCapabilities.maxBodyBytes,",
    "  });",
    "}",
    "",
    "export const fetch = createAgentAppFetchEntrypoint();",
    "",
    "export default { fetch };",
    "",
  ].join("\n");
}

function renderObjectKey(value: string): string {
  return /^[A-Za-z_$][A-Za-z0-9_$]*$/u.test(value) ? value : JSON.stringify(value);
}
