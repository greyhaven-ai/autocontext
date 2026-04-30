function isRecord(value: unknown): value is Record<string, unknown> {
  return value !== null && typeof value === "object" && !Array.isArray(value);
}

function stripJsonComments(raw: string): string {
  let output = "";
  let inString = false;
  let escaped = false;
  for (let i = 0; i < raw.length; i += 1) {
    const ch = raw[i]!;
    const next = raw[i + 1];
    if (inString) {
      output += ch;
      if (escaped) {
        escaped = false;
      } else if (ch === "\\") {
        escaped = true;
      } else if (ch === "\"") {
        inString = false;
      }
      continue;
    }
    if (ch === "\"") {
      inString = true;
      output += ch;
      continue;
    }
    if (ch === "/" && next === "/") {
      while (i < raw.length && raw[i] !== "\n") {
        i += 1;
      }
      output += "\n";
      continue;
    }
    if (ch === "/" && next === "*") {
      i += 2;
      while (i < raw.length && !(raw[i] === "*" && raw[i + 1] === "/")) {
        i += 1;
      }
      i += 1;
      continue;
    }
    output += ch;
  }
  return output;
}

function repairJsonText(raw: string): string {
  return stripJsonComments(raw)
    .replace(/,\s*([}\]])/g, "$1")
    .trim();
}

function tryParseRecord(raw: string): Record<string, unknown> | null {
  for (const candidate of [raw.trim(), repairJsonText(raw)]) {
    if (!candidate) continue;
    try {
      const parsed: unknown = JSON.parse(candidate);
      return isRecord(parsed) ? parsed : null;
    } catch {
      // try the next candidate
    }
  }
  return null;
}

function fencedJsonCandidates(text: string): string[] {
  const candidates: string[] = [];
  const fenceRe = /```(?:json|javascript|js)?\s*([\s\S]*?)```/gi;
  let match: RegExpExecArray | null;
  while ((match = fenceRe.exec(text)) !== null) {
    if (match[1]?.trim()) {
      candidates.push(match[1].trim());
    }
  }
  return candidates;
}

function objectCandidates(text: string): string[] {
  const candidates: string[] = [];
  let depth = 0;
  let start = -1;
  let inString = false;
  let escaped = false;
  for (let i = 0; i < text.length; i += 1) {
    const ch = text[i]!;
    if (inString) {
      if (escaped) {
        escaped = false;
      } else if (ch === "\\") {
        escaped = true;
      } else if (ch === "\"") {
        inString = false;
      }
      continue;
    }
    if (ch === "\"") {
      inString = true;
      continue;
    }
    if (ch === "{") {
      if (depth === 0) {
        start = i;
      }
      depth += 1;
      continue;
    }
    if (ch !== "}" || depth === 0) {
      continue;
    }
    depth -= 1;
    if (depth === 0 && start !== -1) {
      candidates.push(text.slice(start, i + 1));
      start = -1;
    }
  }
  return candidates.sort((a, b) => b.length - a.length);
}

export function parseJsonObjectFromResponse(text: string): Record<string, unknown> | null {
  const trimmed = text.trim();

  const direct = tryParseRecord(trimmed);
  if (direct) {
    return direct;
  }

  for (const candidate of fencedJsonCandidates(trimmed)) {
    const parsed = tryParseRecord(candidate);
    if (parsed) {
      return parsed;
    }
  }

  for (const candidate of objectCandidates(trimmed)) {
    const parsed = tryParseRecord(candidate);
    if (parsed) {
      return parsed;
    }
  }

  const jsonStart = trimmed.indexOf("{");
  const jsonEnd = trimmed.lastIndexOf("}");
  if (jsonStart !== -1 && jsonEnd > jsonStart) {
    const parsed = tryParseRecord(trimmed.slice(jsonStart, jsonEnd + 1));
    if (parsed) {
      return parsed;
    }
  }

  return null;
}

export function parseDelimitedJsonObject(opts: {
  text: string;
  startDelimiter: string;
  endDelimiter: string;
  missingDelimiterLabel: string;
}): Record<string, unknown> {
  const { text, startDelimiter, endDelimiter, missingDelimiterLabel } = opts;
  const startIdx = text.indexOf(startDelimiter);
  const endIdx = text.indexOf(endDelimiter);
  if (startIdx !== -1 && endIdx !== -1 && endIdx > startIdx) {
    const raw = text.slice(startIdx + startDelimiter.length, endIdx).trim();
    const parsed = parseJsonObjectFromResponse(raw);
    if (parsed) {
      return parsed;
    }
    throw new SyntaxError(`response contains invalid ${missingDelimiterLabel} JSON`);
  }

  const parsed = parseJsonObjectFromResponse(text);
  if (parsed) {
    return parsed;
  }

  throw new Error(`response does not contain ${missingDelimiterLabel} delimiters`);
}
