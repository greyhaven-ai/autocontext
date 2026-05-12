"""Shared HTML document shell for derived analytics artifacts."""

from __future__ import annotations

from html import escape


def html_document(title: str, body: str, *, script: str = "") -> str:
    script_block = f"<script>{script}</script>" if script else ""
    return f"""<!doctype html>
<html lang="en">
<head>
  <meta charset="utf-8">
  <meta name="viewport" content="width=device-width, initial-scale=1">
  <title>{_h(title)}</title>
  <style>
    :root {{
      color-scheme: light;
      --bg: #f8fafc;
      --panel: #ffffff;
      --text: #172033;
      --muted: #64748b;
      --border: #d8e0ea;
      --accent: #2563eb;
      --danger: #b42318;
      --warn: #9a6700;
      --ok: #047857;
    }}
    body {{
      margin: 0;
      background: var(--bg);
      color: var(--text);
      font-family: -apple-system, BlinkMacSystemFont, "Segoe UI", sans-serif;
      font-size: 15px;
      line-height: 1.5;
    }}
    main {{
      max-width: 1120px;
      margin: 0 auto;
      padding: 32px 20px 48px;
    }}
    header, section {{
      margin-bottom: 18px;
    }}
    section {{
      background: var(--panel);
      border: 1px solid var(--border);
      border-radius: 8px;
      padding: 18px;
    }}
    h1, h2, h3, p {{
      margin-top: 0;
    }}
    h1 {{
      font-size: 28px;
      line-height: 1.2;
      margin-bottom: 8px;
    }}
    h2 {{
      font-size: 17px;
      margin-bottom: 10px;
    }}
    h3 {{
      font-size: 15px;
      margin-bottom: 4px;
    }}
    ul {{
      padding-left: 20px;
    }}
    pre {{
      white-space: pre-wrap;
      overflow-wrap: anywhere;
      background: #f1f5f9;
      border: 1px solid var(--border);
      border-radius: 6px;
      padding: 12px;
    }}
    .eyebrow {{
      color: var(--accent);
      font-size: 12px;
      font-weight: 700;
      letter-spacing: 0;
      margin-bottom: 4px;
      text-transform: uppercase;
    }}
    .muted, .empty {{
      color: var(--muted);
    }}
    .finding, .event, .curation-item {{
      border-top: 1px solid var(--border);
      padding-top: 12px;
      margin-top: 12px;
    }}
    .finding:first-child, .event:first-child, .curation-item:first-child {{
      border-top: 0;
      padding-top: 0;
      margin-top: 0;
    }}
    .badge {{
      display: inline-block;
      border: 1px solid var(--border);
      border-radius: 999px;
      color: var(--muted);
      font-size: 12px;
      padding: 1px 8px;
      margin-right: 4px;
    }}
    .severity-high, .severity-critical, .severity-error {{
      color: var(--danger);
    }}
    .severity-medium, .severity-warning {{
      color: var(--warn);
    }}
    .severity-low, .severity-info {{
      color: var(--ok);
    }}
    .metric-row {{
      display: grid;
      grid-template-columns: repeat(auto-fit, minmax(140px, 1fr));
      gap: 10px;
    }}
    .metric-row div {{
      background: var(--panel);
      border: 1px solid var(--border);
      border-radius: 8px;
      padding: 14px;
    }}
    .metric-row strong {{
      display: block;
      font-size: 24px;
    }}
    .metric-row span {{
      color: var(--muted);
    }}
    .filters {{
      display: grid;
      grid-template-columns: repeat(auto-fit, minmax(160px, 1fr));
      gap: 10px;
    }}
    input {{
      box-sizing: border-box;
      width: 100%;
      border: 1px solid var(--border);
      border-radius: 6px;
      padding: 8px;
      margin-top: 4px;
    }}
    .grid-two {{
      display: grid;
      grid-template-columns: repeat(auto-fit, minmax(240px, 1fr));
      gap: 16px;
    }}
  </style>
</head>
<body>
  <main>
{body}
  </main>
  {script_block}
</body>
</html>
"""


TIMELINE_FILTER_SCRIPT = """
const inputs = Array.from(document.querySelectorAll("[data-filter]"));
const events = Array.from(document.querySelectorAll(".event"));
function applyFilters() {
  const filters = Object.fromEntries(inputs.map((input) => [input.dataset.filter, input.value.trim().toLowerCase()]));
  for (const event of events) {
    const visible = Object.entries(filters).every(([key, value]) => {
      if (!value) return true;
      return (event.dataset[key] || "").toLowerCase().includes(value);
    });
    event.hidden = !visible;
  }
}
inputs.forEach((input) => input.addEventListener("input", applyFilters));
"""


def _h(value: object) -> str:
    return escape(str(value), quote=True)
