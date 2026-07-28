import { z } from "zod";

import {
  REDACTED_PRESENTATION_VALUE,
  isCredentialShapedPresentationId,
  redactPresentationText,
} from "../security/presentation-redaction.js";

export const AGENT_PROGRESS_NOTE_CAPABILITY = "agent_progress_notes_v1";
export const AGENT_PROGRESS_NOTE_EVENT_NAME = "agent_progress_note";

export const MAX_AGENT_PROGRESS_NOTE_TEXT_LENGTH = 480;
export const MAX_AGENT_PROGRESS_NOTE_EVIDENCE_TARGETS = 5;
export const MAX_AGENT_PROGRESS_NOTE_ID_LENGTH = 200;
export const MAX_RETAINED_AGENT_PROGRESS_NOTE_BYTES = 4 * 1_024;

const SAFE_ID_PATTERN = /^[A-Za-z0-9][A-Za-z0-9._:-]*$/;
const QUOTED_AUTHORIZATION_PATTERN =
  /\b(?:authorization|proxy-authorization)\s*[:=]\s*(?:"[^"]*"|'[^']*')/gi;
const DIGEST_AUTHORIZATION_PATTERN =
  /\b(?:authorization|proxy-authorization)\s*[:=]\s*digest(?:\s+[A-Za-z][A-Za-z0-9_-]*\s*=\s*(?:"[^"]*"|'[^']*'|[^\s,;]+)(?:\s*,\s*[A-Za-z][A-Za-z0-9_-]*\s*=\s*(?:"[^"]*"|'[^']*'|[^\s,;]+))*)?/gi;
const UNQUOTED_AUTHORIZATION_PATTERN =
  /\b(?:authorization|proxy-authorization)\s*[:=]\s*[A-Za-z][A-Za-z0-9._-]*(?:\s+[^\s,;]+)?/gi;
const COOKIE_HEADER_PATTERN = /\b(?:cookie|set-cookie)\s*[:=]\s*(?:"[^"]*"|'[^']*'|[^\r\n]+)/gi;
const SPACED_CREDENTIAL_ASSIGNMENT_PATTERN =
  /\b(?:(?:api|access|private|secret)\s+key|client\s+secret|refresh\s+token|session\s+(?:key|token))\s*[:=]\s*(?:"[^"]*"|'[^']*'|[^\s,;]+)/gi;
const ENV_CREDENTIAL_ASSIGNMENT_PATTERN =
  /(^|[^A-Za-z0-9_])(?:[A-Z][A-Z0-9]*_)*(?:API_KEY|ACCESS_KEY(?:_ID)?|PRIVATE_KEY|SECRET_ACCESS_KEY|SECRET_KEY|CLIENT_SECRET|REFRESH_TOKEN|SESSION_(?:KEY|TOKEN)|AUTH_TOKEN|TOKEN|SECRET|PASSWORD|PASSPHRASE|COOKIE|CREDENTIALS?)\s*[:=]\s*(?:"[^"]*"|'[^']*'|[^\s,;]+)/gi;
const CAMEL_CASE_CREDENTIAL_ASSIGNMENT_PATTERN =
  /(^|[^A-Za-z0-9_])(?:(?:[a-z][A-Za-z0-9]*)(?:ApiKey|AccessKey(?:Id)?|PrivateKey|SecretAccessKey|SecretKey|ClientSecret|RefreshToken|Session(?:Key|Token)|AuthToken|Token|Secret|Password|Passphrase|Cookie|Credentials?)|(?:apiKey|accessKey(?:Id)?|privateKey|secretAccessKey|secretKey|clientSecret|refreshToken|session(?:Key|Token)|authToken|token|secret|password|passphrase|cookie|credentials?))\s*[:=]\s*(?:"[^"]*"|'[^']*'|[^\s,;]+)/g;
const SCHEME_URL_PATTERN = /\b[a-z][a-z0-9+.-]*:\/\/[^\s)>\]}]+/gi;
const NON_HIERARCHICAL_URL_PATTERN = /\b(?:blob|data|javascript|mailto|sms|tel|urn):[^\s)>\]}]+/gi;
const BARE_DOMAIN_PATTERN =
  /\b(?:www\.)?(?:[a-z0-9](?:[a-z0-9-]{0,62}[a-z0-9])?\.)+[a-z]{2,63}(?::[0-9]{1,5})?(?:\/[^\s)>\]}]*)?/gi;
const IPV4_URL_PATTERN =
  /\b(?:[0-9]{1,3}\.){3}[0-9]{1,3}(?:(?::[0-9]{1,5})(?:\/[^\s)>\]}]*)?|\/[^\s)>\]}]*)/g;
const LOCALHOST_URL_PATTERN = /\blocalhost(?:(?::[0-9]{1,5})(?:\/[^\s)>\]}]*)?|\/[^\s)>\]}]*)/gi;
const BRACKETED_IPV6_URL_PATTERN =
  /\[[0-9A-Fa-f:]+(?:%[A-Za-z0-9_.-]+)?\](?:(?::[0-9]{1,5})(?:\/[^\s)>\]}]*)?|\/[^\s)>\]}]*)/g;
const UNBRACKETED_IPV6_URL_PATTERN =
  /(^|[\s(])(?=[0-9A-Fa-f:]*[0-9])(?:(?:[0-9A-Fa-f]{1,4}:){7}[0-9A-Fa-f]{1,4}|[0-9A-Fa-f:]*::[0-9A-Fa-f:]+)(?:\/[^\s)>\]}]*)?/g;
const SELECTOR_ASSIGNMENT_PATTERN =
  /\b(?:css(?:[_-]?selector)?|selector|xpath)\s*[:=]\s*(?:"[^"]*"|'[^']*'|[^\s,;]+)/gi;
const XPATH_PATTERN = /(^|\s)\/{1,2}[A-Za-z*][^\s,;]*/g;
const CSS_PSEUDO_SELECTOR_PATTERN =
  /\b(?:a|article|aside|body|button|details|dialog|div|fieldset|form|h[1-6]|header|html|input|label|li|main|nav|ol|option|p|section|select|span|summary|table|tbody|td|textarea|th|thead|tr|ul)(?::{1,2}(?:active|after|before|checked|disabled|empty|enabled|first-child|first-letter|first-line|first-of-type|focus|focus-visible|focus-within|hover|in-range|invalid|lang|last-child|last-of-type|link|not|nth-child|nth-last-child|nth-last-of-type|nth-of-type|only-child|only-of-type|optional|out-of-range|placeholder|read-only|read-write|required|root|target|valid|visited)(?:\([^)\r\n]*\))?)+/gi;
const CSS_COMBINATOR_PATTERN =
  /\b[A-Za-z][A-Za-z0-9_-]*\s*[>+~]\s*[A-Za-z][A-Za-z0-9_.#:[\]="'()-]*/g;
const CSS_ATTRIBUTE_PATTERN = /\[[A-Za-z_:][^]]*]/g;
const CSS_ID_OR_CLASS_PATTERN = /[.#][A-Za-z_][A-Za-z0-9_-]*/g;
const OMITTED_URL_VALUE = "[URL omitted]";
const OMITTED_SELECTOR_VALUE = "[Selector omitted]";

export const AgentProgressNoteIdSchema = z
  .string()
  .min(1)
  .max(MAX_AGENT_PROGRESS_NOTE_ID_LENGTH)
  .regex(SAFE_ID_PATTERN)
  .refine(
    (value) => !isCredentialShapedPresentationId(value),
    "credential-shaped IDs are not allowed",
  );

export const AgentProgressNoteKindSchema = z.enum([
  "intent",
  "discovery",
  "decision",
  "verification",
  "blocker",
]);

export const AgentProgressNoteActionEvidenceTargetSchema = z
  .object({
    kind: z.literal("action"),
    action_id: AgentProgressNoteIdSchema,
  })
  .strict();

export const AgentProgressNoteArtifactEvidenceTargetSchema = z
  .object({
    kind: z.literal("artifact"),
    action_id: AgentProgressNoteIdSchema,
    artifact_id: AgentProgressNoteIdSchema,
  })
  .strict();

export const AgentProgressNoteEvidenceTargetSchema = z.discriminatedUnion("kind", [
  AgentProgressNoteActionEvidenceTargetSchema,
  AgentProgressNoteArtifactEvidenceTargetSchema,
]);

export const AgentProgressNotePayloadSchema = z
  .object({
    run_id: AgentProgressNoteIdSchema,
    generation: z.number().int().nonnegative(),
    kind: AgentProgressNoteKindSchema,
    text: z.string().trim().min(1).max(MAX_AGENT_PROGRESS_NOTE_TEXT_LENGTH),
    evidence_targets: z
      .array(AgentProgressNoteEvidenceTargetSchema)
      .max(MAX_AGENT_PROGRESS_NOTE_EVIDENCE_TARGETS)
      .optional(),
  })
  .strict()
  .superRefine((value, context) => {
    const identities = new Set<string>();
    for (const [index, target] of (value.evidence_targets ?? []).entries()) {
      const identity = evidenceTargetIdentity(target);
      if (identities.has(identity)) {
        context.addIssue({
          code: z.ZodIssueCode.custom,
          message: "evidence targets must be unique",
          path: ["evidence_targets", index],
        });
      }
      identities.add(identity);
    }
  });

export type AgentProgressNoteKind = z.infer<typeof AgentProgressNoteKindSchema>;
export type AgentProgressNoteEvidenceTarget = z.infer<typeof AgentProgressNoteEvidenceTargetSchema>;
export type AgentProgressNotePayload = z.infer<typeof AgentProgressNotePayloadSchema>;

export interface AgentProgressNoteInput {
  evidenceTargets?: readonly AgentProgressNoteEvidenceTarget[];
  generation: number;
  kind: AgentProgressNoteKind;
  text: string;
}

export interface AgentProgressNotePublisher {
  publish(input: AgentProgressNoteInput): boolean;
}

interface AgentProgressNoteEventSink {
  emit(event: string, payload: Record<string, unknown>): void;
}

export interface CreateAgentProgressNotePublisherOptions {
  events: AgentProgressNoteEventSink;
  runId: string;
}

export function sanitizeAgentProgressNoteText(value: string): string {
  const redacted = redactPresentationText(
    value
      .trim()
      .replace(QUOTED_AUTHORIZATION_PATTERN, REDACTED_PRESENTATION_VALUE)
      .replace(DIGEST_AUTHORIZATION_PATTERN, REDACTED_PRESENTATION_VALUE)
      .replace(UNQUOTED_AUTHORIZATION_PATTERN, REDACTED_PRESENTATION_VALUE)
      .replace(COOKIE_HEADER_PATTERN, REDACTED_PRESENTATION_VALUE)
      .replace(SPACED_CREDENTIAL_ASSIGNMENT_PATTERN, REDACTED_PRESENTATION_VALUE)
      .replace(ENV_CREDENTIAL_ASSIGNMENT_PATTERN, `$1${REDACTED_PRESENTATION_VALUE}`)
      .replace(CAMEL_CASE_CREDENTIAL_ASSIGNMENT_PATTERN, `$1${REDACTED_PRESENTATION_VALUE}`),
  )
    .replace(SCHEME_URL_PATTERN, OMITTED_URL_VALUE)
    .replace(NON_HIERARCHICAL_URL_PATTERN, OMITTED_URL_VALUE)
    .replace(IPV4_URL_PATTERN, OMITTED_URL_VALUE)
    .replace(LOCALHOST_URL_PATTERN, OMITTED_URL_VALUE)
    .replace(BRACKETED_IPV6_URL_PATTERN, OMITTED_URL_VALUE)
    .replace(UNBRACKETED_IPV6_URL_PATTERN, `$1${OMITTED_URL_VALUE}`)
    .replace(SELECTOR_ASSIGNMENT_PATTERN, OMITTED_SELECTOR_VALUE)
    .replace(XPATH_PATTERN, `$1${OMITTED_SELECTOR_VALUE}`)
    .replace(CSS_PSEUDO_SELECTOR_PATTERN, OMITTED_SELECTOR_VALUE)
    .replace(CSS_COMBINATOR_PATTERN, OMITTED_SELECTOR_VALUE)
    .replace(CSS_ATTRIBUTE_PATTERN, OMITTED_SELECTOR_VALUE)
    .replace(BARE_DOMAIN_PATTERN, OMITTED_URL_VALUE)
    .replace(CSS_ID_OR_CLASS_PATTERN, OMITTED_SELECTOR_VALUE)
    .trim();
  if (redacted.length <= MAX_AGENT_PROGRESS_NOTE_TEXT_LENGTH) return redacted;
  return `${redacted.slice(0, MAX_AGENT_PROGRESS_NOTE_TEXT_LENGTH - 1).trimEnd()}…`;
}

export function sanitizeAgentProgressNotePayload(value: unknown): AgentProgressNotePayload | null {
  const parsed = AgentProgressNotePayloadSchema.safeParse(value);
  if (!parsed.success) return null;
  const sanitized = AgentProgressNotePayloadSchema.safeParse({
    ...parsed.data,
    text: sanitizeAgentProgressNoteText(parsed.data.text),
  });
  if (!sanitized.success || !isAgentProgressNotePayloadRetainable(sanitized.data)) {
    return null;
  }
  return sanitized.data;
}

export function isAgentProgressNotePayloadRetainable(payload: AgentProgressNotePayload): boolean {
  return (
    Buffer.byteLength(
      JSON.stringify({
        type: "event",
        event: AGENT_PROGRESS_NOTE_EVENT_NAME,
        payload,
      }),
      "utf-8",
    ) <= MAX_RETAINED_AGENT_PROGRESS_NOTE_BYTES
  );
}

export function createAgentProgressNotePublisher(
  options: CreateAgentProgressNotePublisherOptions,
): AgentProgressNotePublisher | null {
  const runId = AgentProgressNoteIdSchema.safeParse(options.runId);
  if (!runId.success) return null;
  const published = new Set<string>();

  return {
    publish(input) {
      const payload = sanitizeAgentProgressNotePayload({
        run_id: runId.data,
        generation: input.generation,
        kind: input.kind,
        text: input.text,
        ...(input.evidenceTargets === undefined ? {} : { evidence_targets: input.evidenceTargets }),
      });
      if (!payload) return false;
      const fingerprint = progressNoteFingerprint(payload);
      if (published.has(fingerprint)) return false;
      try {
        options.events.emit(AGENT_PROGRESS_NOTE_EVENT_NAME, payload);
      } catch {
        return false;
      }
      published.add(fingerprint);
      return true;
    },
  };
}

function evidenceTargetIdentity(target: AgentProgressNoteEvidenceTarget): string {
  return JSON.stringify([
    target.kind,
    target.action_id,
    target.kind === "artifact" ? target.artifact_id : null,
  ]);
}

function progressNoteFingerprint(payload: AgentProgressNotePayload): string {
  return JSON.stringify([
    payload.run_id,
    payload.generation,
    payload.kind,
    payload.text,
    (payload.evidence_targets ?? []).map(evidenceTargetIdentity).sort(),
  ]);
}
