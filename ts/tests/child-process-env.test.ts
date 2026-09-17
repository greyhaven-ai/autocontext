import { describe, expect, it } from "vitest";
import {
  CONTROL_PLANE_SECRET_ENV_KEYS,
  childProcessEnvWithoutControlPlaneSecrets,
} from "../src/security/child-process-env.js";

describe("child process environment", () => {
  it("removes host control-plane secrets without dropping provider credentials", () => {
    const source: NodeJS.ProcessEnv = {
      PATH: "/usr/bin",
      OPENAI_API_KEY: "provider-secret",
      AutoContext_Server_Token: "mixed-case-secret",
    };
    for (const key of CONTROL_PLANE_SECRET_ENV_KEYS) source[key] = `secret-for-${key}`;

    const child = childProcessEnvWithoutControlPlaneSecrets(source);

    expect(child).toEqual({ PATH: "/usr/bin", OPENAI_API_KEY: "provider-secret" });
    expect(Object.keys(source)).toEqual(
      expect.arrayContaining([...CONTROL_PLANE_SECRET_ENV_KEYS]),
    );
  });
});
