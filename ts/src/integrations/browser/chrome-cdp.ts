import { randomUUID } from "node:crypto";
import type {
  BrowserAction,
  BrowserAuditEvent,
  BrowserFieldKind,
  BrowserPolicyDecision,
  BrowserSessionConfig,
  BrowserSnapshot,
  BrowserSnapshotRef,
  BrowserValidationResult,
} from "./contract/index.js";
import {
  BROWSER_CONTRACT_SCHEMA_VERSION,
  validateBrowserAction,
  validateBrowserAuditEvent,
  validateBrowserSnapshot,
} from "./contract/index.js";
import type { BrowserArtifactPaths } from "./evidence.js";
import { BrowserEvidenceStore } from "./evidence.js";
import { evaluateBrowserActionPolicy } from "./policy.js";
import type { BrowserSessionPort } from "./types.js";

const SNAPSHOT_EXPRESSION = `
(() => {
  const cssEscape = (value) =>
    globalThis.CSS?.escape
      ? CSS.escape(value)
      : String(value).replace(/[^a-zA-Z0-9_-]/g, "\\\\$&");
  const selectorFor = (element) => {
    if (element.id) return "#" + cssEscape(element.id);
    const parts = [];
    let current = element;
    while (current && current.nodeType === Node.ELEMENT_NODE && current !== document.documentElement) {
      const tag = current.tagName.toLowerCase();
      const parent = current.parentElement;
      if (!parent) {
        parts.unshift(tag);
        break;
      }
      const siblings = Array.from(parent.children).filter((sibling) => sibling.tagName === current.tagName);
      const index = siblings.indexOf(current) + 1;
      parts.unshift(siblings.length > 1 ? tag + ":nth-of-type(" + index + ")" : tag);
      current = parent;
      if (parts.length >= 4) break;
    }
    return parts.join(" > ");
  };
  const candidates = Array.from(
    document.querySelectorAll("a,button,input,select,textarea,[role],[tabindex]")
  ).slice(0, 200);
  const refs = candidates.map((element, index) => ({
    id: \`@e\${index + 1}\`,
    role: element.getAttribute("role") ?? element.tagName.toLowerCase(),
    name:
      element.getAttribute("aria-label") ??
      element.getAttribute("name") ??
      element.textContent?.trim() ??
      null,
    text: element.textContent?.trim() ?? null,
    selector: selectorFor(element),
    disabled: element.hasAttribute("disabled"),
  }));
  return {
    url: window.location.href,
    title: document.title ?? "",
    visibleText: document.body?.innerText ?? "",
    refs,
    html: document.documentElement?.outerHTML ?? "",
  };
})()
`.trim();

export interface ChromeCdpTransport {
  send(method: string, params?: Record<string, unknown>): Promise<Record<string, unknown>>;
  close(): Promise<void>;
}

export interface ChromeCdpSessionOpts {
  readonly sessionId: string;
  readonly config: BrowserSessionConfig;
  readonly transport: ChromeCdpTransport;
  readonly evidenceStore?: BrowserEvidenceStore;
}

type BrowserActionFor<TType extends BrowserAction["type"]> = Extract<BrowserAction, { type: TType }>;

export class ChromeCdpSession implements BrowserSessionPort {
  readonly config: BrowserSessionConfig;
  readonly sessionId: string;
  readonly transport: ChromeCdpTransport;
  readonly evidenceStore?: BrowserEvidenceStore;

  private currentUrl = "about:blank";
  private domainsEnabled = false;
  private readonly refSelectors = new Map<string, string>();

  constructor(opts: ChromeCdpSessionOpts) {
    this.sessionId = opts.sessionId;
    this.config = opts.config;
    this.transport = opts.transport;
    this.evidenceStore = opts.evidenceStore;
  }

  async navigate(url: string): Promise<BrowserAuditEvent> {
    const action = buildAction(this.sessionId, "navigate", { url });
    const decision = evaluateBrowserActionPolicy(this.config, action);
    if (!decision.allowed) {
      return this.recordActionResult({
        action,
        decision,
        beforeUrl: this.currentUrl,
        afterUrl: this.currentUrl,
        message: "navigation blocked by browser policy",
      });
    }

    await this.ensureDomainsEnabled();
    const beforeUrl = this.currentUrl;
    await this.transport.send("Page.navigate", { url });
    this.currentUrl = url;
    return this.recordActionResult({
      action,
      decision,
      beforeUrl,
      afterUrl: url,
      message: "navigation allowed",
    });
  }

  async snapshot(): Promise<BrowserSnapshot> {
    const action = buildAction(this.sessionId, "snapshot", {
      captureHtml: true,
      captureScreenshot: this.config.captureScreenshots,
    });
    await this.ensureDomainsEnabled();

    const response = await this.transport.send("Runtime.evaluate", {
      expression: SNAPSHOT_EXPRESSION,
      returnByValue: true,
      awaitPromise: true,
    });
    const payload = extractResultValue(response);
    const refs = extractRefs(payload.refs);

    this.refSelectors.clear();
    for (const ref of refs) {
      if (typeof ref.selector === "string" && ref.selector.length > 0) {
        this.refSelectors.set(ref.id, ref.selector);
      }
    }

    let screenshotBase64: string | null = null;
    if (this.config.captureScreenshots) {
      const screenshotResponse = await this.transport.send("Page.captureScreenshot", { format: "png" });
      screenshotBase64 = typeof screenshotResponse.data === "string" ? screenshotResponse.data : null;
    }

    const artifacts = this.persistSnapshotArtifacts({
      basename: action.actionId,
      html: typeof payload.html === "string" ? payload.html : null,
      screenshotBase64,
    });

    if (typeof payload.url === "string" && payload.url.length > 0) {
      this.currentUrl = payload.url;
    }

    return assertValidDocument(
      "browser snapshot",
      {
        schemaVersion: BROWSER_CONTRACT_SCHEMA_VERSION,
        sessionId: this.sessionId,
        capturedAt: new Date().toISOString(),
        url: this.currentUrl,
        title: typeof payload.title === "string" ? payload.title : "",
        refs,
        visibleText: typeof payload.visibleText === "string" ? payload.visibleText : "",
        htmlPath: artifacts.htmlPath,
        screenshotPath: artifacts.screenshotPath,
      },
      validateBrowserSnapshot,
    );
  }

  async click(ref: string): Promise<BrowserAuditEvent> {
    const action = buildAction(this.sessionId, "click", { ref });
    const decision = evaluateBrowserActionPolicy(this.config, action);
    if (!decision.allowed) {
      return this.recordActionResult({
        action,
        decision,
        beforeUrl: this.currentUrl,
        afterUrl: this.currentUrl,
      });
    }

    await this.ensureDomainsEnabled();
    const beforeUrl = this.currentUrl;
    const selector = this.selectorForRef(ref);
    await this.transport.send("Runtime.evaluate", {
      expression: buildClickExpression(selector),
      returnByValue: true,
      awaitPromise: true,
    });
    return this.recordInteractiveResult({
      action,
      decision,
      beforeUrl,
      message: "click allowed",
    });
  }

  async fill(
    ref: string,
    text: string,
    opts: { fieldKind?: BrowserFieldKind } = {},
  ): Promise<BrowserAuditEvent> {
    const action = buildAction(this.sessionId, "fill", {
      ref,
      text,
      fieldKind: opts.fieldKind,
    });
    const decision = evaluateBrowserActionPolicy(this.config, action);
    if (!decision.allowed) {
      return this.recordActionResult({
        action,
        decision,
        beforeUrl: this.currentUrl,
        afterUrl: this.currentUrl,
        message: "fill blocked by browser policy",
      });
    }

    await this.ensureDomainsEnabled();
    const beforeUrl = this.currentUrl;
    const selector = this.selectorForRef(ref);
    await this.transport.send("Runtime.evaluate", {
      expression: buildFillExpression(selector, text),
      returnByValue: true,
      awaitPromise: true,
    });
    return this.recordInteractiveResult({
      action,
      decision,
      beforeUrl,
      message: "fill allowed",
    });
  }

  async press(key: string): Promise<BrowserAuditEvent> {
    const action = buildAction(this.sessionId, "press", { key });
    const decision = evaluateBrowserActionPolicy(this.config, action);
    if (!decision.allowed) {
      return this.recordActionResult({
        action,
        decision,
        beforeUrl: this.currentUrl,
        afterUrl: this.currentUrl,
      });
    }

    await this.ensureDomainsEnabled();
    const beforeUrl = this.currentUrl;
    await this.transport.send("Runtime.evaluate", {
      expression: buildPressExpression(key),
      returnByValue: true,
      awaitPromise: true,
    });
    return this.recordInteractiveResult({
      action,
      decision,
      beforeUrl,
      message: "key press allowed",
    });
  }

  async screenshot(name: string): Promise<BrowserAuditEvent> {
    const action = buildAction(this.sessionId, "screenshot", { name });
    const decision = evaluateBrowserActionPolicy(this.config, action);
    if (!decision.allowed) {
      return this.recordActionResult({
        action,
        decision,
        beforeUrl: this.currentUrl,
        afterUrl: this.currentUrl,
      });
    }

    await this.ensureDomainsEnabled();
    const response = await this.transport.send("Page.captureScreenshot", { format: "png" });
    const artifacts = this.persistSnapshotArtifacts({
      basename: name,
      screenshotBase64: typeof response.data === "string" ? response.data : null,
    });
    return this.recordActionResult({
      action,
      decision,
      beforeUrl: this.currentUrl,
      afterUrl: this.currentUrl,
      message: "screenshot captured",
      artifacts,
    });
  }

  async close(): Promise<void> {
    await this.transport.close();
  }

  private async ensureDomainsEnabled(): Promise<void> {
    if (this.domainsEnabled) {
      return;
    }
    await this.transport.send("Page.enable", {});
    await this.transport.send("Runtime.enable", {});
    this.domainsEnabled = true;
  }

  private recordActionResult(opts: {
    readonly action: BrowserAction;
    readonly decision: BrowserPolicyDecision;
    readonly beforeUrl: string | null;
    readonly afterUrl: string | null;
    readonly message?: string;
    readonly artifacts?: BrowserArtifactPaths;
  }): BrowserAuditEvent {
    const rawEvent: BrowserAuditEvent = {
      schemaVersion: BROWSER_CONTRACT_SCHEMA_VERSION,
      eventId: newId("evt"),
      sessionId: this.sessionId,
      actionId: opts.action.actionId,
      kind: "action_result",
      allowed: opts.decision.allowed,
      policyReason: opts.decision.reason,
      timestamp: new Date().toISOString(),
      message: opts.message ?? null,
      beforeUrl: opts.beforeUrl,
      afterUrl: opts.afterUrl,
      artifacts: opts.artifacts ?? emptyArtifacts(),
    };
    const event = assertValidDocument("browser audit event", rawEvent, validateBrowserAuditEvent);
    this.evidenceStore?.appendAuditEvent(event);
    return event;
  }

  private persistSnapshotArtifacts(opts: {
    readonly basename: string;
    readonly html?: string | null;
    readonly screenshotBase64?: string | null;
  }): BrowserArtifactPaths {
    return (
      this.evidenceStore?.persistSnapshotArtifacts({
        sessionId: this.sessionId,
        basename: opts.basename,
        html: opts.html,
        screenshotBase64: opts.screenshotBase64,
      }) ?? emptyArtifacts()
    );
  }

  private selectorForRef(ref: string): string {
    return this.refSelectors.get(ref) ?? ref;
  }

  private async recordInteractiveResult(opts: {
    readonly action: BrowserAction;
    readonly decision: BrowserPolicyDecision;
    readonly beforeUrl: string | null;
    readonly message: string;
  }): Promise<BrowserAuditEvent> {
    const afterUrl = await this.readCurrentUrl();
    this.currentUrl = afterUrl;
    const afterDecision = evaluateNavigationUrlPolicy(this.config, afterUrl);
    if (!afterDecision.allowed) {
      return this.recordActionResult({
        action: opts.action,
        decision: afterDecision,
        beforeUrl: opts.beforeUrl,
        afterUrl,
        message: "interaction navigated outside browser policy",
      });
    }
    return this.recordActionResult({
      action: opts.action,
      decision: opts.decision,
      beforeUrl: opts.beforeUrl,
      afterUrl,
      message: opts.message,
    });
  }

  private async readCurrentUrl(): Promise<string> {
    const response = await this.transport.send("Runtime.evaluate", {
      expression: "(() => window.location.href)()",
      returnByValue: true,
      awaitPromise: true,
    });
    const result = response.result;
    if (!isRecord(result)) {
      return this.currentUrl;
    }
    const value = result.value;
    return typeof value === "string" && value.length > 0 ? value : this.currentUrl;
  }
}

function buildAction<TType extends BrowserAction["type"]>(
  sessionId: string,
  type: TType,
  params: BrowserActionFor<TType>["params"],
): BrowserActionFor<TType> {
  const rawAction = {
    schemaVersion: BROWSER_CONTRACT_SCHEMA_VERSION,
    actionId: newId("act"),
    sessionId,
    timestamp: new Date().toISOString(),
    type,
    params,
  } as unknown as BrowserActionFor<TType>;
  return assertValidDocument(
    "browser action",
    rawAction,
    validateBrowserAction,
  );
}

function assertValidDocument<T>(
  label: string,
  value: T,
  validate: (input: unknown) => BrowserValidationResult,
): T {
  const result = validate(value);
  if (!result.valid) {
    throw new TypeError(`invalid ${label}: ${result.errors.join("; ")}`);
  }
  return value;
}

function extractResultValue(response: Record<string, unknown>): Record<string, unknown> {
  const result = response.result;
  if (!isRecord(result)) {
    return {};
  }
  const value = result.value;
  return isRecord(value) ? value : {};
}

function extractRefs(raw: unknown): BrowserSnapshotRef[] {
  if (!Array.isArray(raw)) {
    return [];
  }
  return raw.flatMap((entry) => {
    if (!isRecord(entry) || typeof entry.id !== "string") {
      return [];
    }
    return [
      {
        id: entry.id,
        role: typeof entry.role === "string" ? entry.role : undefined,
        name: typeof entry.name === "string" ? entry.name : undefined,
        text: typeof entry.text === "string" ? entry.text : undefined,
        selector: typeof entry.selector === "string" ? entry.selector : undefined,
        disabled: typeof entry.disabled === "boolean" ? entry.disabled : undefined,
      },
    ];
  });
}

function buildClickExpression(selector: string): string {
  return `
(() => {
  const element = document.querySelector(${JSON.stringify(selector)});
  if (!element) return { ok: false, error: "selector_not_found" };
  element.click();
  return { ok: true };
})()
`.trim();
}

function buildFillExpression(selector: string, text: string): string {
  return `
(() => {
  const element = document.querySelector(${JSON.stringify(selector)});
  if (!element) return { ok: false, error: "selector_not_found" };
  element.focus?.();
  if ("value" in element) {
    element.value = ${JSON.stringify(text)};
  }
  element.dispatchEvent(new Event("input", { bubbles: true }));
  element.dispatchEvent(new Event("change", { bubbles: true }));
  return { ok: true };
})()
`.trim();
}

function buildPressExpression(key: string): string {
  return `
(() => {
  const target = document.activeElement ?? document.body;
  if (!target) return { ok: false, error: "missing_target" };
  target.dispatchEvent(new KeyboardEvent("keydown", { key: ${JSON.stringify(key)}, bubbles: true }));
  target.dispatchEvent(new KeyboardEvent("keyup", { key: ${JSON.stringify(key)}, bubbles: true }));
  return { ok: true };
})()
`.trim();
}

function isRecord(value: unknown): value is Record<string, unknown> {
  return typeof value === "object" && value !== null;
}

function emptyArtifacts(): BrowserArtifactPaths {
  return {
    htmlPath: null,
    screenshotPath: null,
    downloadPath: null,
  };
}

function evaluateNavigationUrlPolicy(
  config: BrowserSessionConfig,
  url: string,
): BrowserPolicyDecision {
  return evaluateBrowserActionPolicy(config, {
    schemaVersion: BROWSER_CONTRACT_SCHEMA_VERSION,
    actionId: "act_interaction_url_probe",
    sessionId: "session_interaction_url_probe",
    timestamp: new Date().toISOString(),
    type: "navigate",
    params: { url },
  });
}

function newId(prefix: string): string {
  return `${prefix}_${randomUUID().replaceAll("-", "")}`;
}
