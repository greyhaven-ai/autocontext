import { describe, expect, it } from "vitest";
import { commandRequiresAuth, httpRequiresAuth } from "../src/server/user-auth/enforcement.js";

describe("commandRequiresAuth", () => {
  it("gates run-affecting commands", () => {
    for (const t of [
      "start_run",
      "create_scenario",
      "confirm_scenario",
      "revise_scenario",
      "cancel_scenario",
      "override_gate",
      "inject_hint",
      "pause",
      "resume",
      "chat_agent",
    ]) {
      expect(commandRequiresAuth(t)).toBe(true);
    }
  });
  it("allows handshake / read-only / provider-auth pre-auth", () => {
    for (const t of [
      "authenticate",
      "list_scenarios",
      "whoami",
      "login",
      "logout",
      "switch_provider",
    ]) {
      expect(commandRequiresAuth(t)).toBe(false);
    }
  });
});

describe("httpRequiresAuth", () => {
  it("gates mutating methods, allows GET/HEAD/OPTIONS", () => {
    for (const m of ["POST", "PUT", "PATCH", "DELETE"]) expect(httpRequiresAuth(m)).toBe(true);
    for (const m of ["GET", "HEAD", "OPTIONS"]) expect(httpRequiresAuth(m)).toBe(false);
  });
});
