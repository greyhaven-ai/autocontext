/**
 * Research adapter contract and domain types (AC-497 TS parity).
 */

export const Urgency = { LOW: "low", NORMAL: "normal", HIGH: "high" } as const;
export type Urgency = (typeof Urgency)[keyof typeof Urgency];

export class ResearchQuery {
  readonly topic: string;
  readonly context: string;
  readonly urgency: Urgency;
  readonly maxResults: number;
  readonly constraints: string[];
  readonly scenarioFamily: string;
  readonly metadata: Record<string, unknown>;

  constructor(opts: {
    topic: string; context?: string; urgency?: Urgency; maxResults?: number;
    constraints?: string[]; scenarioFamily?: string; metadata?: Record<string, unknown>;
  }) {
    this.topic = opts.topic;
    this.context = opts.context ?? "";
    this.urgency = opts.urgency ?? Urgency.NORMAL;
    this.maxResults = opts.maxResults ?? 5;
    this.constraints = opts.constraints ?? [];
    this.scenarioFamily = opts.scenarioFamily ?? "";
    this.metadata = opts.metadata ?? {};
  }
}

export class Citation {
  readonly source: string;
  readonly url: string;
  readonly relevance: number;
  readonly snippet: string;
  readonly retrievedAt: string;

  constructor(opts: { source: string; url?: string; relevance?: number; snippet?: string; retrievedAt?: string }) {
    this.source = opts.source;
    this.url = opts.url ?? "";
    this.relevance = opts.relevance ?? 0;
    this.snippet = opts.snippet ?? "";
    this.retrievedAt = opts.retrievedAt ?? "";
  }

  toJSON(): Record<string, unknown> {
    return { source: this.source, url: this.url, relevance: this.relevance, snippet: this.snippet, retrievedAt: this.retrievedAt };
  }

  static fromJSON(data: Record<string, unknown>): Citation {
    return new Citation({ source: data.source as string, url: data.url as string, relevance: data.relevance as number, snippet: data.snippet as string, retrievedAt: data.retrievedAt as string });
  }
}

export class ResearchResult {
  readonly queryTopic: string;
  readonly summary: string;
  readonly citations: Citation[];
  readonly confidence: number;
  readonly metadata: Record<string, unknown>;

  constructor(opts: {
    queryTopic: string; summary: string; confidence?: number;
    citations?: Citation[]; metadata?: Record<string, unknown>;
  }) {
    this.queryTopic = opts.queryTopic;
    this.summary = opts.summary;
    this.confidence = opts.confidence ?? 0;
    this.citations = opts.citations ?? [];
    this.metadata = opts.metadata ?? {};
  }

  get hasCitations(): boolean { return this.citations.length > 0; }

  toJSON(): Record<string, unknown> {
    return {
      queryTopic: this.queryTopic, summary: this.summary, confidence: this.confidence,
      citations: this.citations.map((c) => c.toJSON()), metadata: this.metadata,
    };
  }

  static fromJSON(data: Record<string, unknown>): ResearchResult {
    return new ResearchResult({
      queryTopic: data.queryTopic as string,
      summary: data.summary as string,
      confidence: data.confidence as number,
      citations: ((data.citations as Record<string, unknown>[]) ?? []).map(Citation.fromJSON),
      metadata: (data.metadata as Record<string, unknown>) ?? {},
    });
  }
}

export interface ResearchAdapter {
  search(query: ResearchQuery): ResearchResult;
}

export class ResearchConfig {
  readonly enabled: boolean;
  readonly adapterName: string;
  readonly maxQueriesPerSession: number;
  readonly maxQueriesPerTurn: number;
  readonly requireCitations: boolean;
  readonly minConfidence: number;

  constructor(opts?: {
    enabled?: boolean; adapterName?: string; maxQueriesPerSession?: number;
    maxQueriesPerTurn?: number; requireCitations?: boolean; minConfidence?: number;
  }) {
    this.enabled = opts?.enabled ?? false;
    this.adapterName = opts?.adapterName ?? "";
    this.maxQueriesPerSession = opts?.maxQueriesPerSession ?? 20;
    this.maxQueriesPerTurn = opts?.maxQueriesPerTurn ?? 3;
    this.requireCitations = opts?.requireCitations ?? true;
    this.minConfidence = opts?.minConfidence ?? 0.3;
  }
}
