import {
  createGenerationAttemptOrchestration,
  type GenerationAttemptOrchestration,
} from "./generation-attempt-orchestrator.js";
import type { GenerationAttempt } from "./generation-attempt-state.js";
import {
  finalizeGenerationCycle,
  getActiveGenerationPhase,
  startNextGeneration,
  type GenerationLoopOrchestration,
} from "./generation-loop-orchestrator.js";
import {
  canContinueGenerationPhase,
  getFinalizedGenerationPhaseAttempt,
  type GenerationPhaseState,
} from "./generation-phase-state.js";
import type { GenerationLoopEventSequenceItem } from "./generation-side-effect-coordinator.js";

export interface GenerationLifecycleWorkflow {
  orchestration: GenerationLoopOrchestration;
  curatorEnabled: boolean;
  maxRetries: number;
  onEvent?: (event: GenerationLoopEventSequenceItem) => void;
  runAttempt: (input: {
    attemptOrchestration: GenerationAttemptOrchestration;
    runId: string;
    generation: number;
    onEvent?: (event: GenerationLoopEventSequenceItem) => void;
  }) => Promise<{
    attemptOrchestration: GenerationAttemptOrchestration;
    events: GenerationLoopEventSequenceItem[];
  }>;
}

export interface GenerationLifecycleWorkflowResult {
  orchestration: GenerationLoopOrchestration;
  attemptOrchestration: GenerationAttemptOrchestration;
  phaseState: GenerationPhaseState;
  generation: number;
  finalizedAttempt: GenerationAttempt;
  events: GenerationLoopEventSequenceItem[];
}

export function createGenerationLifecycleWorkflow(
  workflow: GenerationLifecycleWorkflow,
): GenerationLifecycleWorkflow {
  return workflow;
}

export async function runGenerationLifecycleWorkflow(
  workflow: GenerationLifecycleWorkflow,
): Promise<GenerationLifecycleWorkflowResult> {
  let orchestration = startNextGeneration(
    workflow.orchestration,
    workflow.curatorEnabled,
  );
  let phaseState = getActiveGenerationPhase(orchestration);
  let attemptOrchestration = createGenerationAttemptOrchestration(
    orchestration,
    phaseState,
  );
  const generation = phaseState.generation;
  const events: GenerationLoopEventSequenceItem[] = [
    {
      event: "generation_started",
      payload: orchestration.events.generationStarted!,
    },
    {
      event: "agents_started",
      payload: orchestration.events.agentsStarted!,
    },
  ];
  for (const event of events) workflow.onEvent?.(event);

  while (canContinueGenerationPhase(phaseState, workflow.maxRetries)) {
    const attemptResult = await workflow.runAttempt({
      attemptOrchestration,
      runId: orchestration.runState.runId,
      generation,
      onEvent: workflow.onEvent,
    });
    attemptOrchestration = attemptResult.attemptOrchestration;
    phaseState = attemptOrchestration.phaseState;
    orchestration = attemptOrchestration.orchestration;
    events.push(...attemptResult.events);
  }

  return {
    orchestration,
    attemptOrchestration,
    phaseState,
    generation,
    finalizedAttempt: getFinalizedGenerationPhaseAttempt(phaseState),
    events,
  };
}

export function completeGenerationLifecycleWorkflow(
  workflow: GenerationLifecycleWorkflowResult,
): GenerationLifecycleWorkflowResult {
  const orchestration = finalizeGenerationCycle(
    workflow.orchestration,
    workflow.phaseState,
    {
      runId: workflow.orchestration.runState.runId,
      generation: workflow.generation,
      meanScore: workflow.finalizedAttempt.tournamentResult.meanScore,
      bestScore: workflow.finalizedAttempt.tournamentResult.bestScore,
      elo: workflow.finalizedAttempt.tournamentResult.elo,
      gateDecision: workflow.finalizedAttempt.gateDecision,
    },
  );

  return {
    ...workflow,
    orchestration,
    events: [
      ...workflow.events,
      {
        event: "generation_completed",
        payload: orchestration.events.generationCompleted!,
      },
    ],
  };
}
