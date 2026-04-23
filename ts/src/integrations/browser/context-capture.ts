import { createBrowserRuntimeFromSettings, type BrowserRuntimeSettingsLike } from "./factory.js";

const MAX_BROWSER_VISIBLE_TEXT_CHARS = 1200;

export interface CapturedBrowserContext {
  readonly url: string;
  readonly title: string;
  readonly visibleText: string;
  readonly htmlPath?: string | null;
  readonly screenshotPath?: string | null;
}

export type BrowserContextCaptureSettingsLike = BrowserRuntimeSettingsLike;

export interface CaptureBrowserContextRequest {
  readonly settings: BrowserContextCaptureSettingsLike;
  readonly browserUrl: string;
  readonly evidenceRoot: string;
}

export interface BrowserContextCaptureDependencies {
  readonly createBrowserRuntimeFromSettings: typeof createBrowserRuntimeFromSettings;
}

const DEFAULT_DEPENDENCIES: BrowserContextCaptureDependencies = {
  createBrowserRuntimeFromSettings,
};

export async function captureBrowserContextFromUrl(
  opts: CaptureBrowserContextRequest,
  dependencies: BrowserContextCaptureDependencies = DEFAULT_DEPENDENCIES,
): Promise<CapturedBrowserContext> {
  const configured = dependencies.createBrowserRuntimeFromSettings(opts.settings, {
    evidenceRoot: opts.evidenceRoot,
  });
  if (!configured) {
    throw new Error("browser exploration is disabled");
  }

  const session = await configured.runtime.createSession(configured.sessionConfig);
  try {
    const navigation = await session.navigate(opts.browserUrl);
    if (!navigation.allowed) {
      throw new Error(`browser navigation blocked by policy: ${navigation.policyReason}`);
    }

    const snapshot = await session.snapshot();
    return {
      url: snapshot.url,
      title: snapshot.title,
      visibleText: trimCapturedBrowserText(snapshot.visibleText),
      htmlPath: snapshot.htmlPath ?? null,
      screenshotPath: snapshot.screenshotPath ?? null,
    };
  } finally {
    await session.close();
  }
}

export function renderCapturedBrowserContext(context: CapturedBrowserContext): string {
  const lines = [
    "Live browser context:",
    `URL: ${context.url}`,
    `Title: ${context.title}`,
    `Visible text: ${context.visibleText}`,
  ];
  if (context.htmlPath) {
    lines.push(`HTML artifact: ${context.htmlPath}`);
  }
  if (context.screenshotPath) {
    lines.push(`Screenshot artifact: ${context.screenshotPath}`);
  }
  return lines.join("\n");
}

function trimCapturedBrowserText(text: string): string {
  const normalized = text.replace(/\s+/g, " ").trim();
  return normalized.length <= MAX_BROWSER_VISIBLE_TEXT_CHARS
    ? normalized
    : normalized.slice(0, MAX_BROWSER_VISIBLE_TEXT_CHARS).trimEnd();
}
