/**
 * A2-I Layer 8 — TTY-aware LLM enable resolution (spec §10.2).
 *
 * Pure function. No reads of `process.*` — all environment state is passed in
 * by the caller. This keeps the decision trivially testable across every
 * combination of CLI flag, env var, stdout state, and key availability.
 *
 * Resolution order (first match wins):
 *   1. CLI flag `--enhanced`               → on
 *   2. Env `AUTOCONTEXT_INSTRUMENT_LLM=off` → off
 *   3. Env `AUTOCONTEXT_INSTRUMENT_LLM=on`  → on
 *   4. TTY (stdout) AND key available       → on
 *   5. Otherwise                             → off
 *
 * CI and piped-output contexts default off. Interactive dev sessions default
 * on when a key is present. Matches the pattern adopted by `gh`, `docker`,
 * `git` for similar UX-enhancing features.
 */

export interface EnableEnhancementInputs {
  /** `--enhanced` CLI flag (true → force on, trumps every other signal). */
  readonly cliEnhancedFlag: boolean;
  /** Raw value of `AUTOCONTEXT_INSTRUMENT_LLM` env var ("on" / "off" / undefined). */
  readonly envAutoContextInstrumentLLM: string | undefined;
  /** `process.stdout.isTTY` (typically the caller reads this once at entry). */
  readonly isStdoutTTY: boolean;
  /** Whether an LLM provider key is available (caller checks `ANTHROPIC_API_KEY` or equivalent). */
  readonly hasLLMKey: boolean;
}

/**
 * Resolve the LLM enhancement switch per spec §10.2 order.
 */
export function shouldEnableEnhancement(inputs: EnableEnhancementInputs): boolean {
  // 1. Explicit CLI flag forces on (highest precedence).
  if (inputs.cliEnhancedFlag) return true;

  // 2 / 3. Env var explicit override.
  const envRaw = inputs.envAutoContextInstrumentLLM?.trim().toLowerCase();
  if (envRaw === "off") return false;
  if (envRaw === "on") return true;

  // 4. Auto-enable when interactive and a key is present.
  if (inputs.isStdoutTTY && inputs.hasLLMKey) return true;

  // 5. Default off.
  return false;
}

/**
 * Heuristic for whether an LLM key is present without actually making a call.
 * Checks common environment variables (same set the `providers/` module reads).
 *
 * Accepts a specific env record for testability; defaults to `process.env`.
 */
export function hasAnyLLMKey(env: Readonly<Record<string, string | undefined>> = process.env): boolean {
  return Boolean(
    env.ANTHROPIC_API_KEY
      || env.AUTOCONTEXT_ANTHROPIC_API_KEY
      || env.AUTOCONTEXT_JUDGE_API_KEY
      || env.OPENAI_API_KEY,
  );
}
