import type { AgentAppFetchCatalogPlan } from "./catalog-planner.js";

export interface RenderAgentAppFetchEntrypointTemplateOptions {
  packageSpecifier?: string;
}

const DEFAULT_AGENT_APP_FETCH_PACKAGE_SPECIFIER = "autoctx/control-plane/agent-app-fetch";

export function renderAgentAppFetchEntrypointTemplate(
  plan: AgentAppFetchCatalogPlan,
  options: RenderAgentAppFetchEntrypointTemplateOptions = {},
): string {
  const packageSpecifier = options.packageSpecifier ?? DEFAULT_AGENT_APP_FETCH_PACKAGE_SPECIFIER;
  const moduleMapEntries = plan.entries.map(
    (entry) =>
      `  ${renderObjectKey(entry.name)}: () => import(${JSON.stringify(entry.importSpecifier)}),`,
  );
  const imports = [
    "createAgentAppFetchCatalogFromModuleMap",
    "createAgentAppFetchHandler",
  ].join(", ");
  return [
    `import { ${imports} } from ${JSON.stringify(packageSpecifier)};`,
    "",
    `export const agentAppFetchCatalogPlan = ${JSON.stringify(plan, null, 2)};`,
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
    "export function createAgentAppFetchEntrypoint(hostCapabilities = {}) {",
    "  return createAgentAppFetchHandler({",
    "    catalog: agentAppFetchCatalog,",
    "    env: hostCapabilities.env,",
    "    workspace: hostCapabilities.workspace,",
    "    workspaceStore: hostCapabilities.workspaceStore,",
    "    runtime: hostCapabilities.runtime,",
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
