import { describe, expect, it } from "vitest";
import { userAuthConfigFromEnv } from "../src/server/user-auth/config.js";

describe("userAuthConfigFromEnv", () => {
  it("returns a config when jwks url + issuer are present", () => {
    const cfg = userAuthConfigFromEnv({
      AUTOCONTEXT_AUTH_JWKS_URL: "https://idp.co/jwks",
      AUTOCONTEXT_AUTH_ISSUER: "https://idp.co",
      AUTOCONTEXT_AUTH_AUDIENCE: "autoctx",
    });
    expect(cfg).toEqual({
      jwksUrl: "https://idp.co/jwks",
      issuer: "https://idp.co",
      audience: "autoctx",
    });
  });
  it("audience is optional", () => {
    const cfg = userAuthConfigFromEnv({
      AUTOCONTEXT_AUTH_JWKS_URL: "https://idp.co/jwks",
      AUTOCONTEXT_AUTH_ISSUER: "https://idp.co",
    });
    expect(cfg).toEqual({
      jwksUrl: "https://idp.co/jwks",
      issuer: "https://idp.co",
      audience: undefined,
    });
  });
  it("returns null when disabled (missing jwks or issuer)", () => {
    expect(userAuthConfigFromEnv({})).toBeNull();
    expect(userAuthConfigFromEnv({ AUTOCONTEXT_AUTH_JWKS_URL: "https://idp.co/jwks" })).toBeNull();
    expect(userAuthConfigFromEnv({ AUTOCONTEXT_AUTH_ISSUER: "https://idp.co" })).toBeNull();
  });
});
