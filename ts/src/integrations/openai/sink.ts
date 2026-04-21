/**
 * TraceSink interface + FileSink implementation.
 *
 * Spec §4.1 + §7.2. No beforeExit by default; ``registerBeforeExit: true`` opts in.
 * Mirror of Python ``autocontext.integrations.openai._sink``.
 */

export interface TraceSink {
  add(trace: Record<string, unknown>): void;
  flush(): void;
  close(): void;
}

// FileSink will be implemented in Task 3.2
export class FileSink implements TraceSink {
  add(_trace: Record<string, unknown>): void { throw new Error("stub"); }
  flush(): void { throw new Error("stub"); }
  close(): void { throw new Error("stub"); }
}
