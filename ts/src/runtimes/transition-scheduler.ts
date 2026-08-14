export type RuntimeScheduledTransition =
  | Promise<void>
  | AsyncGenerator<void, void, void>;

export type RuntimeScheduledTransitionFactory = () => RuntimeScheduledTransition;

export interface RuntimeTransitionSchedulerResult {
  readonly seed: number;
  readonly steps: number;
  readonly history: readonly string[];
}

interface ScheduledTask {
  readonly label: string;
  readonly iterator: AsyncGenerator<void, void, void>;
}

/** A seeded, bounded cooperative scheduler for deterministic race tests. */
export class DeterministicRuntimeTransitionScheduler {
  readonly seed: number;
  private state: number;
  private readonly tasks: ScheduledTask[] = [];
  private readonly completedHistory: string[] = [];

  constructor(seed: number) {
    if (!Number.isSafeInteger(seed)) throw new Error("scheduler seed must be a safe integer");
    this.seed = seed;
    this.state = (seed >>> 0) || 0x9e3779b9;
  }

  get pendingCount(): number {
    return this.tasks.length;
  }

  schedule(label: string, factory: RuntimeScheduledTransitionFactory): void {
    const normalized = label.trim();
    if (!normalized) throw new Error("scheduled transition label must be non-empty");
    this.tasks.push({ label: normalized, iterator: scheduledIterator(factory) });
  }

  async runUntilQuiescent(maxSteps = 1_000): Promise<RuntimeTransitionSchedulerResult> {
    if (!Number.isSafeInteger(maxSteps) || maxSteps < 1) {
      throw new Error("scheduler maxSteps must be a positive safe integer");
    }
    let steps = 0;
    while (this.tasks.length > 0) {
      if (steps >= maxSteps) {
        throw new Error(`runtime transition scheduler exceeded ${maxSteps} steps`);
      }
      const index = this.nextUint32() % this.tasks.length;
      const task = this.tasks[index]!;
      const result = await task.iterator.next();
      this.completedHistory.push(task.label);
      steps += 1;
      if (result.done) this.tasks.splice(index, 1);
    }
    return { seed: this.seed, steps, history: [...this.completedHistory] };
  }

  private nextUint32(): number {
    let value = this.state;
    value ^= value << 13;
    value ^= value >>> 17;
    value ^= value << 5;
    this.state = value >>> 0;
    return this.state;
  }
}

async function* scheduledIterator(
  factory: RuntimeScheduledTransitionFactory,
): AsyncGenerator<void, void, void> {
  const transition = factory();
  if (isAsyncGenerator(transition)) {
    for await (const _boundary of transition) yield;
    return;
  }
  await transition;
}

function isAsyncGenerator(
  value: RuntimeScheduledTransition,
): value is AsyncGenerator<void, void, void> {
  return Symbol.asyncIterator in value;
}
