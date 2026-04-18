// Payload schema for prompt-patch actuator.
//
// The payload directory contains exactly one file:
//   prompt.txt — UTF-8 text with the system prompt body.
//
// The "parsed" payload shape is just the string content of prompt.txt.

import { z } from "zod";

export const PromptPatchPayloadSchema = z.string().min(0);

export type PromptPatchPayload = z.infer<typeof PromptPatchPayloadSchema>;

export const PROMPT_PATCH_FILENAME = "prompt.txt";
