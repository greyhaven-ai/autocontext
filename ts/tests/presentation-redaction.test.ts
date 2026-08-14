import { describe, expect, it } from "vitest";

import {
  REDACTED_PRESENTATION_VALUE,
  redactPresentationText,
} from "../src/security/presentation-redaction.js";

describe("presentation redaction", () => {
  it.each([
    ["Bearer authorization", "Authorization: Bearer bearer-credential", "bearer-credential"],
    ["Basic authorization", "Authorization: Basic QWxhZGRpbjpvcGVuIHNlc2FtZQ==", "QWxhZGRpbjpvcGVuIHNlc2FtZQ=="],
    ["quoted password", 'password="correct horse battery staple"', "correct horse battery staple"],
  ])("redacts %s without leaving a credential suffix", (_label, input, credential) => {
    const redacted = redactPresentationText(input);
    expect(redacted).toContain(REDACTED_PRESENTATION_VALUE);
    expect(redacted).not.toContain(credential);
  });

  it("redacts quoted JSON credential keys and values containing spaces", () => {
    const redacted = redactPresentationText(
      '{"api_key": "api value with spaces", "token": "token value with spaces"}',
    );

    expect(redacted).not.toContain("api value with spaces");
    expect(redacted).not.toContain("token value with spaces");
    expect(redacted.match(/\[Redacted\]/g)).toHaveLength(2);
  });

  it("redacts provider-prefixed environment keys and plain auth assignments", () => {
    const redacted = redactPresentationText(
      "OPENAI_API_KEY=sk-supersecret ANTHROPIC_API_KEY=anthropic-secret " +
      "GITHUB_TOKEN=github-secret auth=auth-secret bearer=bearer-secret",
    );
    for (const secret of [
      "sk-supersecret",
      "anthropic-secret",
      "github-secret",
      "auth-secret",
      "bearer-secret",
    ]) {
      expect(redacted).not.toContain(secret);
    }
  });

  it("redacts human-readable labels and percent-encoded query keys", () => {
    const redacted = redactPresentationText(
      "API key: hunter2 " +
      "https://example.test/?api%5Fkey=query-secret&%74oken=fragment-secret#safe=value",
    );

    expect(redacted).not.toContain("hunter2");
    expect(redacted).not.toContain("query-secret");
    expect(redacted).not.toContain("fragment-secret");
    expect(redacted).toContain("safe=value");
  });
});
