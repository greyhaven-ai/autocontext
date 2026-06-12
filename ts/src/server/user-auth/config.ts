export interface UserAuthConfig {
  jwksUrl: string;
  issuer: string;
  audience?: string;
}

/**
 * Build user-auth config from env. Returns null (auth disabled, the default
 * local-mode behavior) unless both the JWKS URL and issuer are set.
 */
export function userAuthConfigFromEnv(
  env: Record<string, string | undefined>,
): UserAuthConfig | null {
  const jwksUrl = env.AUTOCONTEXT_AUTH_JWKS_URL?.trim();
  const issuer = env.AUTOCONTEXT_AUTH_ISSUER?.trim();
  if (!jwksUrl || !issuer) return null;
  const audience = env.AUTOCONTEXT_AUTH_AUDIENCE?.trim() || undefined;
  return { jwksUrl, issuer, audience };
}
