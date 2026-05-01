/**
 * Model router — tier-based model selection (AC-345 Task 16).
 * Mirrors Python's autocontext/agents/model_router.py.
 */

const TIER_CONFIG_DEFAULTS = {
  enabled: false,
  tierHaikuModel: "claude-haiku-4-5-20251001",
  tierSonnetModel: "claude-sonnet-4-5-20250929",
  tierOpusModel: "claude-opus-4-6",
  competitorHaikuMaxGen: 3,
  competitorRetryEscalation: 1,
  coachMinTier: "sonnet",
  architectMinTier: "opus",
  analystMinTier: "haiku",
  translatorMinTier: "haiku",
};

export type TierConfigOpts = Partial<typeof TIER_CONFIG_DEFAULTS>;

export class TierConfig {
  readonly enabled: boolean;
  readonly tierHaikuModel: string;
  readonly tierSonnetModel: string;
  readonly tierOpusModel: string;
  readonly competitorHaikuMaxGen: number;
  readonly competitorRetryEscalation: number;
  readonly coachMinTier: string;
  readonly architectMinTier: string;
  readonly analystMinTier: string;
  readonly translatorMinTier: string;

  constructor(opts: TierConfigOpts = {}) {
    const resolved = { ...TIER_CONFIG_DEFAULTS, ...opts };
    this.enabled = resolved.enabled;
    this.tierHaikuModel = resolved.tierHaikuModel;
    this.tierSonnetModel = resolved.tierSonnetModel;
    this.tierOpusModel = resolved.tierOpusModel;
    this.competitorHaikuMaxGen = resolved.competitorHaikuMaxGen;
    this.competitorRetryEscalation = resolved.competitorRetryEscalation;
    this.coachMinTier = resolved.coachMinTier;
    this.architectMinTier = resolved.architectMinTier;
    this.analystMinTier = resolved.analystMinTier;
    this.translatorMinTier = resolved.translatorMinTier;
  }
}

export interface SelectOpts {
  generation: number;
  retryCount: number;
  isPlateau: boolean;
}

const TIER_ORDER = ["haiku", "sonnet", "opus"] as const;

export class ModelRouter {
  #config: TierConfig;
  #tierMap: Record<string, string>;

  constructor(config: TierConfig) {
    this.#config = config;
    this.#tierMap = {
      haiku: config.tierHaikuModel,
      sonnet: config.tierSonnetModel,
      opus: config.tierOpusModel,
    };
  }

  select(role: string, opts: SelectOpts): string | null {
    if (!this.#config.enabled) return null;

    const minTiers: Record<string, string> = {
      analyst: this.#config.analystMinTier,
      coach: this.#config.coachMinTier,
      architect: this.#config.architectMinTier,
      translator: this.#config.translatorMinTier,
      curator: "opus",
    };

    let tier = minTiers[role] ?? "sonnet";

    if (role === "competitor") {
      tier = opts.generation <= this.#config.competitorHaikuMaxGen ? "haiku" : "sonnet";

      if (opts.retryCount >= this.#config.competitorRetryEscalation) {
        tier = this.#maxTier(tier, "sonnet");
      }
      if (opts.isPlateau) {
        tier = "opus";
      }
    } else if (role === "analyst" || role === "coach") {
      if (opts.isPlateau) {
        tier = this.#maxTier(tier, "opus");
      }
    }

    return this.#tierMap[tier] ?? null;
  }

  #maxTier(a: string, b: string): string {
    const ai = TIER_ORDER.indexOf(a as (typeof TIER_ORDER)[number]);
    const bi = TIER_ORDER.indexOf(b as (typeof TIER_ORDER)[number]);
    return ai >= bi ? a : b;
  }
}
