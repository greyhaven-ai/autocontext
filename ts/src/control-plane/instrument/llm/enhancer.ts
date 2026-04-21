/**
 * A2-I Layer 8 — LLM enhancer with silent fallback (spec §10.5).
 *
 * Wraps an LLM provider call with:
 *   - Opt-out short-circuit (`enabled: false` → return default immediately)
 *   - Timeout via Promise.race
 *   - Malformed-output fallback (empty / whitespace-only → default)
 *   - Never-throws invariant — all error paths resolve to `defaultNarrative`
 *
 * Diagnostics surface via the optional `onDiagnostic` callback so callers can
 * route them (visible under `--output json`; quiet on `pretty`). The enhancer
 * itself never logs directly.
 *
 * Reproducibility discipline (spec §5.4): LLM-enhanced narrative lands in
 * `pr-body.md` only. `plan.json` hashing runs earlier in the pipeline and
 * never sees enhancer output. Whether enhancement is on or off, `plan.json`
 * is byte-identical for the same inputs.
 */

/**
 * Minimal provider shape this module depends on. Keeps us decoupled from the
 * full `providers/LLMProvider` surface while remaining structurally compatible
 * (every provider in the Foundation B factory satisfies this shape).
 */
export interface EnhancerProvider {
  /**
   * Produce a completion for the given prompt. Returns the model's text output.
   * Throws on network / rate-limit / upstream-error. The enhancer converts
   * throws to the default-narrative fallback; providers never need to handle
   * it themselves.
   */
  complete(opts: { prompt: string; signal?: AbortSignal }): Promise<string>;
}

export type EnhancerDiagnostic =
  | { kind: "disabled" }
  | { kind: "no-provider" }
  | { kind: "timeout"; timeoutMs: number }
  | { kind: "provider-error"; message: string }
  | { kind: "malformed-output"; received: string }
  | { kind: "ok"; chars: number };

export interface EnhanceOpts<C> {
  readonly defaultNarrative: string;
  readonly context: C;
  readonly prompt: (ctx: C) => string;
  readonly enabled: boolean;
  readonly provider?: EnhancerProvider;
  readonly timeoutMs?: number;
  readonly onDiagnostic?: (d: EnhancerDiagnostic) => void;
}

const DEFAULT_TIMEOUT_MS = 10_000;

/**
 * Attempt an LLM-enhanced narrative; fall back to `defaultNarrative` on any
 * failure. Never throws.
 */
export async function enhance<C>(opts: EnhanceOpts<C>): Promise<string> {
  const diag = opts.onDiagnostic ?? (() => {});

  // 1. Disabled → immediate default, no work.
  if (!opts.enabled) {
    diag({ kind: "disabled" });
    return opts.defaultNarrative;
  }

  // 2. No provider supplied (enhancement enabled but nothing to call).
  if (!opts.provider) {
    diag({ kind: "no-provider" });
    return opts.defaultNarrative;
  }

  const promptText = opts.prompt(opts.context);
  const timeoutMs = opts.timeoutMs ?? DEFAULT_TIMEOUT_MS;

  // 3. Race the provider call against a timeout.
  const abortController = new AbortController();
  const timer = new Promise<"timeout">((resolve) => {
    const id = setTimeout(() => {
      abortController.abort();
      resolve("timeout");
    }, timeoutMs);
    // Allow garbage collection of the timer when the race settles via the
    // provider branch.
    id.unref?.();
  });

  let output: string;
  try {
    const result = await Promise.race([
      opts.provider.complete({ prompt: promptText, signal: abortController.signal }),
      timer,
    ]);
    if (result === "timeout") {
      diag({ kind: "timeout", timeoutMs });
      return opts.defaultNarrative;
    }
    output = result;
  } catch (err) {
    const msg = err instanceof Error ? err.message : String(err);
    diag({ kind: "provider-error", message: msg });
    return opts.defaultNarrative;
  }

  // 4. Sanitize — empty / whitespace-only → default.
  const trimmed = output.trim();
  if (trimmed.length === 0) {
    diag({ kind: "malformed-output", received: output });
    return opts.defaultNarrative;
  }

  diag({ kind: "ok", chars: trimmed.length });
  return trimmed;
}
