import { describe, expect, it } from "vitest";
import { parseClientMessage } from "../src/server/protocol.js";

describe("authenticate command", () => {
  it("parses a valid authenticate frame", () => {
    expect(parseClientMessage({ type: "authenticate", token: "abc" })).toEqual({
      type: "authenticate",
      token: "abc",
    });
  });
  it("rejects a blank token", () => {
    expect(() => parseClientMessage({ type: "authenticate", token: "" })).toThrow();
  });
});
