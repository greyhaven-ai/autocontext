/**
 * Shared per-request context passed to the extracted route handlers
 * (AC-852). Built once per request in InteractiveServer#handleHttpRequest.
 */

import type { ServerResponse } from "node:http";

export interface HttpRouteContext {
  url: string;
  method: string;
  requestUrl: URL;
  res: ServerResponse;
  json: (status: number, body: unknown) => void;
  readJsonBody: () => Promise<Record<string, unknown>>;
}
