/** Host control-plane credentials must not cross into agent CLI children. */
export const CONTROL_PLANE_SECRET_ENV_KEYS = [
  "AUTOCONTEXT_SERVER_AUTH_KEYS",
  "AUTOCONTEXT_SERVER_CREDENTIALS_FILE",
  "AUTOCONTEXT_SERVER_TOKEN",
  "AUTOCONTEXT_SERVER_TOKEN_FILE",
] as const;

const CONTROL_PLANE_SECRET_ENV_KEY_SET = new Set<string>(CONTROL_PLANE_SECRET_ENV_KEYS);

export function isControlPlaneSecretEnvKey(key: string): boolean {
  return CONTROL_PLANE_SECRET_ENV_KEY_SET.has(key.toUpperCase());
}

export function childProcessEnvWithoutControlPlaneSecrets(
  source: NodeJS.ProcessEnv = process.env,
): NodeJS.ProcessEnv {
  const environment: NodeJS.ProcessEnv = {};
  for (const [key, value] of Object.entries(source)) {
    if (!isControlPlaneSecretEnvKey(key)) environment[key] = value;
  }
  return environment;
}

/**
 * Permanently consume control-plane credentials before loading or invoking
 * in-process, project-owned modules. Restoring them around async work would
 * create a process-wide race in concurrent servers.
 */
export function clearControlPlaneSecretsFromCurrentProcess(): void {
  for (const key of Object.keys(process.env)) {
    if (isControlPlaneSecretEnvKey(key)) delete process.env[key];
  }
}
