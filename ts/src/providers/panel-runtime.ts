import {
  ProviderError,
  type CompletionOptions,
  type CompletionResult,
  type LLMProvider,
} from "../types/index.js";

export interface PanelParticipant {
  provider: string;
  model: string;
}

export interface PanelConfig {
  role: string;
  participants: PanelParticipant[];
  synthesizerProvider: string;
  synthesizerModel: string;
}

export interface PanelSettings {
  panelRoles?: string;
  panelParticipants?: string;
  panelSynthesizerProvider?: string;
  panelSynthesizerModel?: string;
}

export type PanelProviderFactory = (providerType: string, model: string) => LLMProvider;

function splitCsv(value = ""): string[] {
  return value.split(",").map((part) => part.trim()).filter(Boolean);
}

function parseParticipant(value: string): PanelParticipant {
  const [provider, ...modelParts] = value.split(":");
  if (modelParts.length === 0) return { provider: "", model: provider.trim() };
  return { provider: provider.trim(), model: modelParts.join(":").trim() };
}

function participantsForRole(spec: string | undefined, role: string): PanelParticipant[] {
  if (!spec?.trim()) return [];
  let selected = "";
  for (const chunk of spec.split(";").map((part) => part.trim()).filter(Boolean)) {
    const separator = chunk.indexOf("=");
    if (separator === -1) {
      selected = chunk;
      continue;
    }
    if (chunk.slice(0, separator).trim() === role) {
      selected = chunk.slice(separator + 1);
      break;
    }
  }
  return splitCsv(selected).map(parseParticipant);
}

export function parsePanelConfigForRole(settings: PanelSettings, role: string): PanelConfig | null {
  if (!splitCsv(settings.panelRoles).includes(role)) return null;
  const participants = participantsForRole(settings.panelParticipants, role);
  if (participants.length === 0) return null;
  return {
    role,
    participants,
    synthesizerProvider: settings.panelSynthesizerProvider?.trim() ?? "",
    synthesizerModel: settings.panelSynthesizerModel?.trim() ?? "",
  };
}

export class PanelProvider implements LLMProvider {
  readonly name: string;
  readonly supportsThinkingStream = false;

  constructor(
    private readonly opts: {
      role: string;
      baseProvider: LLMProvider;
      config: PanelConfig;
      providerFactory?: PanelProviderFactory;
    },
  ) {
    this.name = `panel:${opts.role}`;
  }

  defaultModel(): string {
    return this.opts.config.synthesizerModel || this.opts.baseProvider.defaultModel();
  }

  supportsImageAttachments(): boolean {
    return false;
  }

  close(): void {
    this.opts.baseProvider.close?.();
  }

  async complete(callOpts: CompletionOptions): Promise<CompletionResult> {
    if (callOpts.imageAttachments?.length) {
      throw new ProviderError("Panel providers do not support image attachments");
    }
    const started = Date.now();
    const participants = [] as Array<Record<string, unknown>>;
    let totalCost = 0;

    for (const participant of this.opts.config.participants) {
      const provider = this.providerFor(participant.provider, participant.model);
      const participantStarted = Date.now();
      try {
        const result = await provider.complete({ ...callOpts, model: participant.model });
        totalCost += result.costUsd ?? 0;
        participants.push({
          provider: participant.provider,
          model: participant.model,
          content: result.text.trim(),
          usage: result.usage,
          latencyMs: Date.now() - participantStarted,
          estimatedCostUsd: result.costUsd ?? 0,
        });
      } finally {
        if (provider !== this.opts.baseProvider) provider.close?.();
      }
    }

    const model = this.opts.config.synthesizerModel || callOpts.model || this.opts.baseProvider.defaultModel();
    const synthProvider = this.providerFor(this.opts.config.synthesizerProvider, model);
    const synthStarted = Date.now();
    let synth: CompletionResult;
    try {
      synth = await synthProvider.complete({
        ...callOpts,
        model,
        userPrompt: synthesisPrompt(this.opts.role, callOpts.userPrompt, participants),
      });
    } finally {
      if (synthProvider !== this.opts.baseProvider) synthProvider.close?.();
    }
    totalCost += synth.costUsd ?? 0;

    return {
      ...synth,
      costUsd: round(totalCost),
      metadata: {
        ...synth.metadata,
        panelRuntime: true,
        panelRole: this.opts.role,
        panelParticipants: participants,
        panelSynthesizer: {
          provider: this.opts.config.synthesizerProvider,
          model,
          content: synth.text.trim(),
          usage: synth.usage,
          latencyMs: Date.now() - synthStarted,
          estimatedCostUsd: synth.costUsd ?? 0,
        },
        panelLatencyMs: Date.now() - started,
        panelEstimatedCostUsd: round(totalCost),
      },
    };
  }

  private providerFor(providerType: string, model: string): LLMProvider {
    if (!providerType || !this.opts.providerFactory) return this.opts.baseProvider;
    return this.opts.providerFactory(providerType, model);
  }
}

function synthesisPrompt(role: string, originalPrompt: string, participants: Array<Record<string, unknown>>): string {
  const outputs = participants
    .map((item, index) => `[${index + 1}] ${item.provider}:${item.model}\n${item.content}`)
    .join("\n\n");
  return [
    `You are synthesizing an experimental model panel for the ${role} role.`,
    "Return one final role response that preserves the expected contract.",
    "",
    "Original prompt:",
    originalPrompt,
    "",
    "Participant outputs:",
    outputs,
  ].join("\n");
}

function round(value: number): number {
  return Number(value.toFixed(6));
}

export function comparePanelBenchmark(input: {
  singleScore: number;
  panelScore: number;
  singleLatencyMs: number;
  panelLatencyMs: number;
  singleCostUsd: number;
  panelCostUsd: number;
}): Record<string, number> {
  const singleScorePerCost = input.singleCostUsd > 0 ? input.singleScore / input.singleCostUsd : 0;
  const panelScorePerCost = input.panelCostUsd > 0 ? input.panelScore / input.panelCostUsd : 0;
  return {
    scoreDelta: round(input.panelScore - input.singleScore),
    latencyMsDelta: round(input.panelLatencyMs - input.singleLatencyMs),
    costUsdDelta: round(input.panelCostUsd - input.singleCostUsd),
    scorePerCostDelta: round(panelScorePerCost - singleScorePerCost),
  };
}
