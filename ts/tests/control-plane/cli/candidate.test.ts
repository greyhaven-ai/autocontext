import { describe, test, expect, beforeEach, afterEach } from "vitest";
import { mkdtempSync, rmSync, mkdirSync, writeFileSync } from "node:fs";
import { tmpdir } from "node:os";
import { join } from "node:path";
import { runControlPlaneCommand } from "../../../src/control-plane/cli/index.js";

let tmp: string;
let payload: string;

beforeEach(() => {
  tmp = mkdtempSync(join(tmpdir(), "autocontext-cli-cand-"));
  payload = join(tmp, "payload");
  mkdirSync(payload, { recursive: true });
  writeFileSync(join(payload, "prompt.txt"), "You are a helpful agent.\n");
});

afterEach(() => {
  rmSync(tmp, { recursive: true, force: true });
});

describe("candidate --help", () => {
  test("prints help and exits 0", async () => {
    const r = await runControlPlaneCommand(["candidate", "--help"], { cwd: tmp });
    expect(r.exitCode).toBe(0);
    expect(r.stdout.toLowerCase()).toContain("candidate");
    expect(r.stdout).toContain("register");
    expect(r.stdout).toContain("list");
    expect(r.stdout).toContain("show");
    expect(r.stdout).toContain("lineage");
    expect(r.stdout).toContain("rollback");
  });
});

describe("candidate register", () => {
  test("registers a prompt-patch artifact from a payload directory and prints its id (json)", async () => {
    const r = await runControlPlaneCommand(
      [
        "candidate",
        "register",
        "--scenario",
        "grid_ctf",
        "--actuator",
        "prompt-patch",
        "--payload",
        payload,
        "--output",
        "json",
      ],
      { cwd: tmp },
    );
    expect(r.exitCode).toBe(0);
    const parsed = JSON.parse(r.stdout);
    expect(parsed.id).toMatch(/^[0-9A-HJKMNP-TV-Z]{26}$/);
    expect(parsed.actuatorType).toBe("prompt-patch");
    expect(parsed.scenario).toBe("grid_ctf");
    expect(parsed.activationState).toBe("candidate");
  });

  test("rejects invalid scenario format", async () => {
    const r = await runControlPlaneCommand(
      [
        "candidate",
        "register",
        "--scenario",
        "Bad Slug!",
        "--actuator",
        "prompt-patch",
        "--payload",
        payload,
      ],
      { cwd: tmp },
    );
    expect(r.exitCode).not.toBe(0);
    expect(r.stderr.toLowerCase()).toMatch(/scenario/);
  });

  test("rejects unknown actuator type", async () => {
    const r = await runControlPlaneCommand(
      [
        "candidate",
        "register",
        "--scenario",
        "grid_ctf",
        "--actuator",
        "unknown-actuator",
        "--payload",
        payload,
      ],
      { cwd: tmp },
    );
    expect(r.exitCode).not.toBe(0);
    expect(r.stderr.toLowerCase()).toContain("actuator");
  });

  test("rejects missing payload path", async () => {
    const r = await runControlPlaneCommand(
      [
        "candidate",
        "register",
        "--scenario",
        "grid_ctf",
        "--actuator",
        "prompt-patch",
        "--payload",
        join(tmp, "does-not-exist"),
      ],
      { cwd: tmp },
    );
    expect(r.exitCode).not.toBe(0);
  });
});

describe("candidate list / show / lineage", () => {
  test("list returns candidates after register", async () => {
    const rReg = await runControlPlaneCommand(
      ["candidate", "register", "--scenario", "grid_ctf", "--actuator", "prompt-patch", "--payload", payload, "--output", "json"],
      { cwd: tmp },
    );
    const registered = JSON.parse(rReg.stdout);

    const rList = await runControlPlaneCommand(
      ["candidate", "list", "--output", "json"],
      { cwd: tmp },
    );
    expect(rList.exitCode).toBe(0);
    const list = JSON.parse(rList.stdout);
    expect(Array.isArray(list)).toBe(true);
    expect(list.map((a: { id: string }) => a.id)).toContain(registered.id);
  });

  test("show round-trips the registered artifact", async () => {
    const rReg = await runControlPlaneCommand(
      ["candidate", "register", "--scenario", "grid_ctf", "--actuator", "prompt-patch", "--payload", payload, "--output", "json"],
      { cwd: tmp },
    );
    const registered = JSON.parse(rReg.stdout);

    const rShow = await runControlPlaneCommand(
      ["candidate", "show", registered.id, "--output", "json"],
      { cwd: tmp },
    );
    expect(rShow.exitCode).toBe(0);
    const shown = JSON.parse(rShow.stdout);
    expect(shown.id).toBe(registered.id);
    expect(shown.payloadHash).toBe(registered.payloadHash);
  });

  test("lineage renders a tree for an artifact with no parents", async () => {
    const rReg = await runControlPlaneCommand(
      ["candidate", "register", "--scenario", "grid_ctf", "--actuator", "prompt-patch", "--payload", payload, "--output", "json"],
      { cwd: tmp },
    );
    const registered = JSON.parse(rReg.stdout);

    const rLin = await runControlPlaneCommand(
      ["candidate", "lineage", registered.id],
      { cwd: tmp },
    );
    expect(rLin.exitCode).toBe(0);
    expect(rLin.stdout).toContain(registered.id);
  });
});

describe("candidate rollback", () => {
  test("records a rollback event on a non-routing-rule artifact", async () => {
    const rReg = await runControlPlaneCommand(
      ["candidate", "register", "--scenario", "grid_ctf", "--actuator", "prompt-patch", "--payload", payload, "--output", "json"],
      { cwd: tmp },
    );
    const registered = JSON.parse(rReg.stdout);
    // Promote to shadow first so rollback → candidate is allowed (candidate→candidate is not).
    const rApply = await runControlPlaneCommand(
      ["promotion", "apply", registered.id, "--to", "shadow", "--reason", "initial-eval"],
      { cwd: tmp },
    );
    expect(rApply.exitCode).toBe(0);

    const rRb = await runControlPlaneCommand(
      ["candidate", "rollback", registered.id, "--reason", "test-rollback"],
      { cwd: tmp },
    );
    expect(rRb.exitCode).toBe(0);
  });
});
