// Payload schema for the fine-tuned-model actuator.
//
// The payload directory contains exactly one file:
//   pointer.json — a pointer to a model checkpoint living on external storage.
//
// Rationale: the actual model bytes are far too large to live in the content-
// addressable payload tree, so the artifact's payload is a small pointer
// document. `checkpointHash` is the content address of the external checkpoint;
// consumers must verify it after fetching.
//
// v1 shape:
//   { kind: "model-checkpoint",
//     externalPath: string,       // e.g. "s3://bucket/path" or "/mnt/models/ckpt"
//     checkpointHash: ContentHash,
//     family: string,             // model family slug ("llama-3", ...)
//     backend: string }           // e.g. "mlx", "cuda"

import { z } from "zod";

const ContentHashRe = /^sha256:[0-9a-f]{64}$/;

export const FineTunedModelPayloadSchema = z
  .object({
    kind: z.literal("model-checkpoint"),
    externalPath: z.string().min(1),
    checkpointHash: z.string().regex(ContentHashRe, "checkpointHash must be sha256:<64 hex>"),
    family: z.string().min(1),
    backend: z.string().min(1),
  })
  .strict();

export type FineTunedModelPayload = z.infer<typeof FineTunedModelPayloadSchema>;

export const FINE_TUNED_MODEL_FILENAME = "pointer.json";
