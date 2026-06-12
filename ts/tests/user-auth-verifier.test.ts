import { describe, expect, it } from "vitest";
import { SignJWT, exportJWK, generateKeyPair } from "jose";
import {
  createVerifierWithKeySet,
  identityFromClaims,
} from "../src/server/user-auth/token-verifier.js";

async function setup() {
  const { publicKey, privateKey } = await generateKeyPair("RS256");
  const jwk = await exportJWK(publicKey);
  jwk.kid = "test";
  jwk.alg = "RS256";
  return { privateKey, jwks: { keys: [jwk] } };
}

function sign(
  privateKey: CryptoKey,
  claims: Record<string, unknown>,
  opts: { iss: string; aud?: string; exp?: string },
) {
  let b = new SignJWT(claims)
    .setProtectedHeader({ alg: "RS256", kid: "test" })
    .setIssuer(opts.iss)
    .setIssuedAt();
  if (opts.aud) b = b.setAudience(opts.aud);
  return b.setExpirationTime(opts.exp ?? "5m").sign(privateKey);
}

describe("identityFromClaims", () => {
  it("extracts subject, email, groups (groups then roles, default [])", () => {
    expect(identityFromClaims({ sub: "u1", email: "u1@x.co", groups: ["a"] })).toEqual({
      subject: "u1",
      email: "u1@x.co",
      groups: ["a"],
    });
    expect(identityFromClaims({ sub: "u2", roles: ["r"] })).toEqual({
      subject: "u2",
      email: undefined,
      groups: ["r"],
    });
    expect(identityFromClaims({ sub: "u3" })).toEqual({
      subject: "u3",
      email: undefined,
      groups: [],
    });
  });
});

describe("verifier", () => {
  it("accepts a validly signed token", async () => {
    const { privateKey, jwks } = await setup();
    const v = createVerifierWithKeySet(jwks, { issuer: "https://idp.co", audience: "autoctx" });
    const token = await sign(
      privateKey,
      { sub: "u1", email: "u1@x.co", groups: ["eng"] },
      { iss: "https://idp.co", aud: "autoctx" },
    );
    expect(await v.verify(token)).toEqual({ subject: "u1", email: "u1@x.co", groups: ["eng"] });
  });
  it("rejects a wrong issuer", async () => {
    const { privateKey, jwks } = await setup();
    const v = createVerifierWithKeySet(jwks, { issuer: "https://idp.co", audience: "autoctx" });
    const token = await sign(privateKey, { sub: "u1" }, { iss: "https://evil.co", aud: "autoctx" });
    await expect(v.verify(token)).rejects.toThrow();
  });
  it("rejects a wrong audience", async () => {
    const { privateKey, jwks } = await setup();
    const v = createVerifierWithKeySet(jwks, { issuer: "https://idp.co", audience: "autoctx" });
    const token = await sign(privateKey, { sub: "u1" }, { iss: "https://idp.co", aud: "other" });
    await expect(v.verify(token)).rejects.toThrow();
  });
  it("rejects an expired token", async () => {
    const { privateKey, jwks } = await setup();
    const v = createVerifierWithKeySet(jwks, { issuer: "https://idp.co", audience: "autoctx" });
    const token = await sign(
      privateKey,
      { sub: "u1" },
      { iss: "https://idp.co", aud: "autoctx", exp: "-1m" },
    );
    await expect(v.verify(token)).rejects.toThrow();
  });
});
