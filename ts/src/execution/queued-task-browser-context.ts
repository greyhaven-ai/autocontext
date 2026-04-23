import { join, resolve } from "node:path";

import {
  captureBrowserContextFromUrl,
  renderCapturedBrowserContext,
  type BrowserContextCaptureSettingsLike,
} from "../integrations/browser/context-capture.js";

export interface QueuedTaskBrowserContextRequest {
  readonly taskId: string;
  readonly browserUrl: string;
  readonly referenceContext?: string;
}

export interface QueuedTaskBrowserContextService {
  buildReferenceContext(request: QueuedTaskBrowserContextRequest): Promise<string>;
}

export interface QueuedTaskBrowserContextSettingsLike extends BrowserContextCaptureSettingsLike {
  readonly runsRoot: string;
}

export interface QueuedTaskBrowserContextDependencies {
  readonly captureBrowserContextFromUrl: typeof captureBrowserContextFromUrl;
}

const DEFAULT_DEPENDENCIES: QueuedTaskBrowserContextDependencies = {
  captureBrowserContextFromUrl,
};

export class SettingsBackedQueuedTaskBrowserContextService implements QueuedTaskBrowserContextService {
  readonly #settings: QueuedTaskBrowserContextSettingsLike;
  readonly #dependencies: QueuedTaskBrowserContextDependencies;

  constructor(
    settings: QueuedTaskBrowserContextSettingsLike,
    dependencies: QueuedTaskBrowserContextDependencies = DEFAULT_DEPENDENCIES,
  ) {
    this.#settings = settings;
    this.#dependencies = dependencies;
  }

  async buildReferenceContext(request: QueuedTaskBrowserContextRequest): Promise<string> {
    const context = await this.#dependencies.captureBrowserContextFromUrl({
      settings: this.#settings,
      browserUrl: request.browserUrl,
      evidenceRoot: join(resolve(this.#settings.runsRoot), "task_queue", request.taskId),
    });
    return mergeQueuedTaskReferenceContext(
      request.referenceContext,
      renderCapturedBrowserContext(context),
    );
  }
}

export function createQueuedTaskBrowserContextService(
  settings: QueuedTaskBrowserContextSettingsLike,
  dependencies: QueuedTaskBrowserContextDependencies = DEFAULT_DEPENDENCIES,
): QueuedTaskBrowserContextService {
  return new SettingsBackedQueuedTaskBrowserContextService(settings, dependencies);
}

export function mergeQueuedTaskReferenceContext(
  referenceContext: string | undefined,
  browserContext: string,
): string {
  const trimmedReferenceContext = referenceContext?.trim();
  const trimmedBrowserContext = browserContext.trim();
  return [trimmedReferenceContext, trimmedBrowserContext].filter(Boolean).join("\n\n");
}
