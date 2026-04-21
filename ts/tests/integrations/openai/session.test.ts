/**
 * autocontextSession AsyncLocalStorage tests — Task 3.3.
 * Mirrors Python contextvar session tests.
 */
import { describe, test, expect } from "vitest";
import { autocontextSession, currentSession } from "../../../src/integrations/openai/session.js";

describe("autocontextSession", () => {
  test("defaults to empty session outside any context", () => {
    const s = currentSession();
    expect(s).toEqual({});
  });

  test("sets userId + sessionId within block", async () => {
    await autocontextSession({ userId: "u1", sessionId: "s1" }, async () => {
      const s = currentSession();
      expect(s.userId).toBe("u1");
      expect(s.sessionId).toBe("s1");
    });
  });

  test("propagates across await", async () => {
    await autocontextSession({ userId: "u2" }, async () => {
      await Promise.resolve();
      expect(currentSession().userId).toBe("u2");
    });
  });

  test("propagates across setTimeout via promise", async () => {
    await autocontextSession({ userId: "u3" }, async () => {
      const result = await new Promise<string>((resolve) => {
        setTimeout(() => resolve(currentSession().userId ?? ""), 10);
      });
      expect(result).toBe("u3");
    });
  });

  test("propagates across Promise.all branches", async () => {
    const results: string[] = [];
    await autocontextSession({ userId: "u4" }, async () => {
      await Promise.all([
        Promise.resolve().then(() => { results.push(currentSession().userId ?? ""); }),
        Promise.resolve().then(() => { results.push(currentSession().userId ?? ""); }),
      ]);
    });
    expect(results).toEqual(["u4", "u4"]);
  });

  test("restores empty session after block exits", async () => {
    await autocontextSession({ userId: "u5" }, async () => {});
    expect(currentSession()).toEqual({});
  });

  test("nested sessions shadow outer ones", async () => {
    await autocontextSession({ userId: "outer" }, async () => {
      await autocontextSession({ userId: "inner" }, async () => {
        expect(currentSession().userId).toBe("inner");
      });
      expect(currentSession().userId).toBe("outer");
    });
  });

  test("userId-only context has no sessionId", async () => {
    await autocontextSession({ userId: "u6" }, async () => {
      const s = currentSession();
      expect(s.userId).toBe("u6");
      expect(s.sessionId).toBeUndefined();
    });
  });

  test("sessionId-only context has no userId", async () => {
    await autocontextSession({ sessionId: "s7" }, async () => {
      const s = currentSession();
      expect(s.sessionId).toBe("s7");
      expect(s.userId).toBeUndefined();
    });
  });
});
