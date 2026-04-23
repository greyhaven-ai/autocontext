import { join, resolve } from "node:path";

import {
  captureBrowserContextFromUrl,
  renderCapturedBrowserContext,
  type BrowserContextCaptureSettingsLike,
  type CapturedBrowserContext,
} from "../integrations/browser/context-capture.js";
import type { Evidence } from "./investigation-contracts.js";

export interface InvestigationBrowserContext extends CapturedBrowserContext {}

export interface InvestigationBrowserContextSettingsLike extends BrowserContextCaptureSettingsLike {
  readonly knowledgeRoot: string;
}

export interface CaptureInvestigationBrowserContextRequest {
  readonly settings: InvestigationBrowserContextSettingsLike;
  readonly browserUrl: string;
  readonly investigationName: string;
}

export interface InvestigationBrowserContextDependencies {
  readonly captureBrowserContextFromUrl: typeof captureBrowserContextFromUrl;
}

const DEFAULT_DEPENDENCIES: InvestigationBrowserContextDependencies = {
  captureBrowserContextFromUrl,
};

export async function captureInvestigationBrowserContext(
  opts: CaptureInvestigationBrowserContextRequest,
  dependencies: InvestigationBrowserContextDependencies = DEFAULT_DEPENDENCIES,
): Promise<InvestigationBrowserContext> {
  return dependencies.captureBrowserContextFromUrl({
    settings: opts.settings,
    browserUrl: opts.browserUrl,
    evidenceRoot: join(resolve(opts.settings.knowledgeRoot), "_investigations", opts.investigationName),
  });
}

export function renderInvestigationBrowserContext(context: InvestigationBrowserContext): string {
  return renderCapturedBrowserContext(context);
}

export function buildInvestigationBrowserEvidence(context: InvestigationBrowserContext): Evidence {
  return {
    id: "browser_snapshot",
    kind: "browser_snapshot",
    source: context.url,
    summary: buildInvestigationBrowserSummary(context),
    supports: [],
    contradicts: [],
    isRedHerring: false,
  };
}

export function buildInvestigationBrowserSummary(context: InvestigationBrowserContext): string {
  if (context.title && context.visibleText) {
    return `${context.title}\n${context.visibleText}`;
  }
  return context.title || context.visibleText || context.url;
}
