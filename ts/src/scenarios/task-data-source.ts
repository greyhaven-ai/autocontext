import { z } from "zod";

export const TASK_DATA_SOURCE_SCHEMA_VERSION = 1;
export const MAX_TASK_DATA_SOURCES = 8;
export const MAX_TASK_DATA_SOURCE_ID_CHARACTERS = 200;
export const MAX_TASK_DATA_SOURCE_CONTENT_CHARACTERS = 256_000;
export const MAX_TASK_DATA_SOURCE_CONTENT_TOTAL_CHARACTERS = 512_000;

export const TASK_DATA_SOURCE_ROLES = [
  "target",
  "input",
  "reference",
  "constraint",
  "example",
  "eval",
  "holdout",
] as const;

export const TaskDataSourceRoleSchema = z.enum(TASK_DATA_SOURCE_ROLES);

const NonEmptyTextSchema = z.string().trim().min(1);
export const TaskDataSourceIdSchema = NonEmptyTextSchema.max(
  MAX_TASK_DATA_SOURCE_ID_CHARACTERS,
  `task data source id must not exceed ${MAX_TASK_DATA_SOURCE_ID_CHARACTERS} characters`,
);

/** Describes where the retained task data originated. */
export const TaskDataSourceProvenanceSchema = z
  .object({
    origin: NonEmptyTextSchema,
    sourceRef: NonEmptyTextSchema.optional(),
    collectedAt: z.string().datetime({ message: "collectedAt must be ISO 8601 format" }).optional(),
    metadata: z.record(z.unknown()).default({}),
  })
  .strict();

/** Describes the retained bytes addressed by a task data source. */
export const TaskDataSourceIntegritySchema = z
  .object({
    contentHash: z
      .string()
      .regex(/^sha256:[0-9a-f]{64}$/, "contentHash must be sha256:<64 lowercase hex>"),
    byteLength: z.number().int().nonnegative(),
    sourceByteLength: z.number().int().nonnegative().optional(),
    truncated: z.boolean().default(false),
  })
  .strict()
  .superRefine((integrity, ctx) => {
    if (
      integrity.sourceByteLength !== undefined &&
      integrity.sourceByteLength < integrity.byteLength
    ) {
      ctx.addIssue({
        code: z.ZodIssueCode.custom,
        message: "sourceByteLength must be greater than or equal to byteLength",
        path: ["sourceByteLength"],
      });
    }
    if (
      integrity.truncated &&
      (integrity.sourceByteLength === undefined ||
        integrity.sourceByteLength <= integrity.byteLength)
    ) {
      ctx.addIssue({
        code: z.ZodIssueCode.custom,
        message: "truncated data must declare a sourceByteLength greater than byteLength",
        path: ["sourceByteLength"],
      });
    }
    if (
      !integrity.truncated &&
      integrity.sourceByteLength !== undefined &&
      integrity.sourceByteLength > integrity.byteLength
    ) {
      ctx.addIssue({
        code: z.ZodIssueCode.custom,
        message: "truncated must be true when sourceByteLength is greater than byteLength",
        path: ["truncated"],
      });
    }
  });

/**
 * Generic manifest entry for data attached to an improvement task.
 *
 * `contentRef` is an opaque locator for the retained bytes. It may point to a
 * blob store, local file, URL, or another resolver owned by the caller.
 */
export const TaskDataSourceSchema = z
  .object({
    schemaVersion: z
      .literal(TASK_DATA_SOURCE_SCHEMA_VERSION)
      .default(TASK_DATA_SOURCE_SCHEMA_VERSION),
    id: TaskDataSourceIdSchema,
    role: TaskDataSourceRoleSchema,
    name: NonEmptyTextSchema.optional(),
    contentRef: NonEmptyTextSchema,
    mediaType: NonEmptyTextSchema.optional(),
    provenance: TaskDataSourceProvenanceSchema,
    integrity: TaskDataSourceIntegritySchema,
  })
  .strict();

/** Inline content resolved for one manifest entry at an execution boundary. */
export const TaskDataSourceContentSchema = z
  .object({
    sourceId: TaskDataSourceIdSchema,
    content: z
      .string()
      .max(
        MAX_TASK_DATA_SOURCE_CONTENT_CHARACTERS,
        `task data source content must not exceed ${MAX_TASK_DATA_SOURCE_CONTENT_CHARACTERS} characters`,
      ),
  })
  .strict();

/** A bounded set of inline contents resolved at an execution boundary. */
export const TaskDataSourceContentListSchema = z
  .array(TaskDataSourceContentSchema)
  .max(
    MAX_TASK_DATA_SOURCES,
    `task data source content list must not exceed ${MAX_TASK_DATA_SOURCES} entries`,
  )
  .superRefine((contents, ctx) => {
    const sourceIds = new Set<string>();
    let totalCharacters = 0;
    contents.forEach((content, index) => {
      if (sourceIds.has(content.sourceId)) {
        ctx.addIssue({
          code: z.ZodIssueCode.custom,
          message: `duplicate resolved task data source id: ${content.sourceId}`,
          path: [index, "sourceId"],
        });
      }
      sourceIds.add(content.sourceId);
      totalCharacters += content.content.length;
    });
    if (totalCharacters > MAX_TASK_DATA_SOURCE_CONTENT_TOTAL_CHARACTERS) {
      ctx.addIssue({
        code: z.ZodIssueCode.custom,
        message:
          "resolved task data source content must not exceed " +
          `${MAX_TASK_DATA_SOURCE_CONTENT_TOTAL_CHARACTERS} characters in total`,
        path: [],
      });
    }
  });

export type TaskDataSourceRole = z.infer<typeof TaskDataSourceRoleSchema>;
export type TaskDataSourceProvenance = z.infer<typeof TaskDataSourceProvenanceSchema>;
export type TaskDataSourceIntegrity = z.infer<typeof TaskDataSourceIntegritySchema>;
export type TaskDataSource = z.infer<typeof TaskDataSourceSchema>;
export type TaskDataSourceContent = z.infer<typeof TaskDataSourceContentSchema>;
