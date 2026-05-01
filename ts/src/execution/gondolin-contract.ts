export interface GondolinSecretRef {
  name: string;
  envVar: string;
}

export interface GondolinSandboxPolicy {
  allowNetwork: boolean;
  allowedEgressHosts: string[];
  readOnlyMounts: string[];
  writableMounts: string[];
  secrets: GondolinSecretRef[];
  timeoutSeconds: number;
}

export interface GondolinExecutionRequest {
  scenarioName: string;
  strategy: Record<string, unknown>;
  seed: number;
  policy: GondolinSandboxPolicy;
}

export interface GondolinExecutionResult {
  result: Record<string, unknown>;
  replay: Record<string, unknown>;
  stdout?: string;
  stderr?: string;
}

export interface GondolinBackend {
  execute(request: GondolinExecutionRequest): Promise<GondolinExecutionResult>;
}

export function createDefaultGondolinSandboxPolicy(
  overrides: Partial<GondolinSandboxPolicy> = {},
): GondolinSandboxPolicy {
  return {
    allowNetwork: false,
    allowedEgressHosts: [],
    readOnlyMounts: [],
    writableMounts: [],
    secrets: [],
    timeoutSeconds: 30,
    ...overrides,
  };
}
