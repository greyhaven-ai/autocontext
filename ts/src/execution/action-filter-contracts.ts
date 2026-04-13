import { z } from "zod";

export const ActionDictSchema = z
  .object({
    action: z.string(),
    description: z.string(),
    type: z.string().optional(),
    range: z.tuple([z.number(), z.number()]).optional(),
    row: z.number().optional(),
    col: z.number().optional(),
  })
  .passthrough();

export type ActionDict = z.infer<typeof ActionDictSchema>;

export interface ScenarioLike {
  enumerateLegalActions(state: Record<string, unknown>): ActionDict[] | null;
  validateActions(
    state: Record<string, unknown>,
    playerId: string,
    actions: Record<string, unknown>,
  ): [boolean, string];
}

export interface HarnessLoaderLike {
  validators: Array<{
    enumerate_legal_actions?: (state: Record<string, unknown>) => ActionDict[];
  }>;
}
