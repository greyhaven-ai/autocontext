/* eslint-disable */
// AUTO-GENERATED from src/integrations/browser/contract/json-schemas/ — DO NOT EDIT.
// Regenerate with: node scripts/generate-browser-contract-types.mjs
// CI gate: node scripts/generate-browser-contract-types.mjs --check

// ---- browser-action.schema.json ----
export type BrowserAction =
  | {
      schemaVersion: "1.0";
      actionId: string;
      sessionId: string;
      timestamp: string;
      type: "navigate";
      params: {
        url: string;
      };
    }
  | {
      schemaVersion: "1.0";
      actionId: string;
      sessionId: string;
      timestamp: string;
      type: "snapshot";
      params: {
        captureHtml?: boolean;
        captureScreenshot?: boolean;
      };
    }
  | {
      schemaVersion: "1.0";
      actionId: string;
      sessionId: string;
      timestamp: string;
      type: "click";
      params: {
        ref: string;
      };
    }
  | {
      schemaVersion: "1.0";
      actionId: string;
      sessionId: string;
      timestamp: string;
      type: "fill";
      params: {
        ref: string;
        text: string;
        fieldKind?: "text" | "email" | "password" | "search" | "other";
      };
    }
  | {
      schemaVersion: "1.0";
      actionId: string;
      sessionId: string;
      timestamp: string;
      type: "press";
      params: {
        key: string;
      };
    }
  | {
      schemaVersion: "1.0";
      actionId: string;
      sessionId: string;
      timestamp: string;
      type: "screenshot";
      params: {
        name: string;
      };
    };

// ---- browser-audit-event.schema.json ----
export interface BrowserAuditEvent {
  schemaVersion: "1.0";
  eventId: string;
  sessionId: string;
  actionId: string;
  kind: "action_result";
  allowed: boolean;
  policyReason:
    | "allowed"
    | "domain_not_allowed"
    | "auth_blocked"
    | "uploads_blocked"
    | "downloads_blocked"
    | "missing_uploads_root"
    | "missing_downloads_root"
    | "user_profile_requires_auth"
    | "invalid_url";
  timestamp: string;
  message?: string | null;
  beforeUrl?: string | null;
  afterUrl?: string | null;
  artifacts: {
    htmlPath: string | null;
    screenshotPath: string | null;
    downloadPath: string | null;
  };
}

// ---- browser-session-config.schema.json ----
export type BrowserSessionConfig = {
  schemaVersion: "1.0";
  profileMode: "ephemeral" | "isolated" | "user-profile";
  allowedDomains: string[];
  allowAuth: boolean;
  allowUploads: boolean;
  allowDownloads: boolean;
  captureScreenshots: boolean;
  headless: boolean;
  downloadsRoot: string | null;
  uploadsRoot: string | null;
};

// ---- browser-snapshot.schema.json ----
export interface BrowserSnapshot {
  schemaVersion: "1.0";
  sessionId: string;
  capturedAt: string;
  url: string;
  title: string;
  refs: {
    id: string;
    role?: string;
    name?: string;
    text?: string;
    selector?: string;
    disabled?: boolean;
  }[];
  visibleText: string;
  htmlPath: string | null;
  screenshotPath: string | null;
}

// ---- shared-defs.schema.json ----
export interface BrowserSharedDefs {
  [k: string]: unknown;
}
