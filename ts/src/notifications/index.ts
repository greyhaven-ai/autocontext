/**
 * Notification system — stdout, HTTP, Slack, composite, callback notifiers (AC-349 Task 37).
 * Mirrors Python's autocontext/notifications/ package.
 */

// ---------------------------------------------------------------------------
// Types
// ---------------------------------------------------------------------------

export type EventType = "threshold_met" | "regression" | "completion" | "failure";

export interface NotificationEvent {
  type: EventType;
  taskName: string;
  taskId: string;
  score: number;
  previousBest?: number;
  roundCount?: number;
  costUsd?: number;
  outputPreview?: string;
  error?: string;
  metadata?: Record<string, unknown>;
}

export interface Notifier {
  notify(event: NotificationEvent): Promise<void>;
}

// ---------------------------------------------------------------------------
// StdoutNotifier
// ---------------------------------------------------------------------------

export class StdoutNotifier implements Notifier {
  #logger: (msg: string) => void;

  constructor(logger?: (msg: string) => void) {
    this.#logger = logger ?? console.log;
  }

  async notify(event: NotificationEvent): Promise<void> {
    const parts = [
      `[${event.type}]`,
      `task=${event.taskName}`,
      `score=${event.score.toFixed(4)}`,
    ];
    if (event.roundCount != null) parts.push(`rounds=${event.roundCount}`);
    if (event.error) parts.push(`error=${event.error}`);
    this.#logger(parts.join(" "));
  }
}

// ---------------------------------------------------------------------------
// CallbackNotifier
// ---------------------------------------------------------------------------

export class CallbackNotifier implements Notifier {
  #callback: (event: NotificationEvent) => void;

  constructor(callback: (event: NotificationEvent) => void) {
    this.#callback = callback;
  }

  async notify(event: NotificationEvent): Promise<void> {
    this.#callback(event);
  }
}

// ---------------------------------------------------------------------------
// CompositeNotifier
// ---------------------------------------------------------------------------

export class CompositeNotifier implements Notifier {
  #notifiers: Notifier[];
  #eventFilter?: Set<EventType>;

  constructor(notifiers: Notifier[], eventFilter?: EventType[]) {
    this.#notifiers = notifiers;
    this.#eventFilter = eventFilter ? new Set(eventFilter) : undefined;
  }

  async notify(event: NotificationEvent): Promise<void> {
    if (this.#eventFilter && !this.#eventFilter.has(event.type)) return;

    await Promise.all(
      this.#notifiers.map((n) =>
        n.notify(event).catch(() => {
          // Notifier errors must never crash the loop
        }),
      ),
    );
  }
}

// ---------------------------------------------------------------------------
// HTTPNotifier
// ---------------------------------------------------------------------------

export class HTTPNotifier implements Notifier {
  #url: string;
  #headers: Record<string, string>;

  constructor(url: string, headers?: Record<string, string>) {
    this.#url = url;
    this.#headers = headers ?? {};
  }

  async notify(event: NotificationEvent): Promise<void> {
    await fetch(this.#url, {
      method: "POST",
      headers: { "Content-Type": "application/json", ...this.#headers },
      body: JSON.stringify(event),
    });
  }
}

// ---------------------------------------------------------------------------
// SlackWebhookNotifier
// ---------------------------------------------------------------------------

const EMOJI_MAP: Record<string, string> = {
  threshold_met: ":white_check_mark:",
  regression: ":warning:",
  completion: ":checkered_flag:",
  failure: ":x:",
};

export class SlackWebhookNotifier implements Notifier {
  #webhookUrl: string;

  constructor(webhookUrl: string) {
    this.#webhookUrl = webhookUrl;
  }

  async notify(event: NotificationEvent): Promise<void> {
    const emoji = EMOJI_MAP[event.type] ?? ":bell:";
    const blocks = [
      {
        type: "header",
        text: { type: "plain_text", text: `${emoji} ${event.type}: ${event.taskName}` },
      },
      {
        type: "section",
        text: { type: "mrkdwn", text: `Score: *${event.score.toFixed(4)}*` },
      },
    ];

    if (event.outputPreview) {
      blocks.push({
        type: "section",
        text: { type: "mrkdwn", text: `\`\`\`${event.outputPreview.slice(0, 500)}\`\`\`` },
      });
    }

    await fetch(this.#webhookUrl, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ blocks }),
    });
  }
}
