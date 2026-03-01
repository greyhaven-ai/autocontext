/**
 * Multi-strategy judge response parser.
 *
 * Strategies (tried in order):
 * 1. Marker-based: <!-- JUDGE_RESULT_START/END -->
 * 2. Code block: ```json ... ```
 * 3. Raw JSON: { "score": ... } anywhere in text
 * 4. Plain text: "Score: 0.85" patterns
 */
const RESULT_START = "<!-- JUDGE_RESULT_START -->";
const RESULT_END = "<!-- JUDGE_RESULT_END -->";
function clamp(v) {
    return Math.max(0, Math.min(1, v));
}
function extractFromDict(data, source) {
    const raw = Number(data.score ?? 0);
    const score = clamp(isNaN(raw) ? 0 : raw);
    let reasoning = String(data.reasoning ?? "");
    if (source !== "markers") {
        reasoning = `[${source} parse] ${reasoning}`;
    }
    const dims = {};
    const dimensions = data.dimensions;
    if (dimensions && typeof dimensions === "object") {
        for (const [k, v] of Object.entries(dimensions)) {
            const n = Number(v);
            if (!isNaN(n))
                dims[k] = clamp(n);
        }
    }
    return { score, reasoning, dimensionScores: dims };
}
function tryMarkerParse(response) {
    const startIdx = response.indexOf(RESULT_START);
    if (startIdx === -1)
        return null;
    const endIdx = response.indexOf(RESULT_END, startIdx);
    if (endIdx === -1)
        return null;
    const jsonStr = response
        .slice(startIdx + RESULT_START.length, endIdx)
        .trim();
    try {
        const data = JSON.parse(jsonStr);
        return typeof data === "object" && data !== null ? data : null;
    }
    catch {
        return null;
    }
}
function tryCodeBlockParse(response) {
    const re = /```(?:json)?\s*\n?(.*?)\n?```/gs;
    let match;
    while ((match = re.exec(response)) !== null) {
        try {
            const data = JSON.parse(match[1].trim());
            if (typeof data === "object" && data !== null && "score" in data) {
                return data;
            }
        }
        catch {
            continue;
        }
    }
    return null;
}
function tryRawJsonParse(response) {
    // Simple flat objects
    const flatRe = /\{[^{}]*"score"[^{}]*\}/g;
    let match;
    while ((match = flatRe.exec(response)) !== null) {
        try {
            const data = JSON.parse(match[0]);
            if (typeof data === "object" && "score" in data)
                return data;
        }
        catch {
            continue;
        }
    }
    // Nested objects (with dimensions)
    const nestedRe = /\{(?:[^{}]|\{[^{}]*\})*"score"(?:[^{}]|\{[^{}]*\})*\}/g;
    while ((match = nestedRe.exec(response)) !== null) {
        try {
            const data = JSON.parse(match[0]);
            if (typeof data === "object" && "score" in data)
                return data;
        }
        catch {
            continue;
        }
    }
    return null;
}
function tryPlaintextParse(response) {
    const patterns = [
        /(?:overall\s+)?score[:\s]+([01](?:\.\d+)?)/i,
        /"score"\s*:\s*([01](?:\.\d+)?)/,
        /(\d\.\d+)\s*\/\s*1\.0/,
    ];
    for (const pat of patterns) {
        const m = response.match(pat);
        if (m) {
            const score = parseFloat(m[1]);
            if (score >= 0 && score <= 1) {
                const reasoning = response.length > 500 ? response.slice(0, 500) : response;
                return {
                    score,
                    reasoning: `[plaintext parse] ${reasoning}`,
                    dimensionScores: {},
                };
            }
        }
    }
    return null;
}
export function parseJudgeResponse(response) {
    // Strategy 1: Markers
    const markerData = tryMarkerParse(response);
    if (markerData)
        return extractFromDict(markerData, "markers");
    // Strategy 2: Code block
    const codeData = tryCodeBlockParse(response);
    if (codeData)
        return extractFromDict(codeData, "code_block");
    // Strategy 3: Raw JSON
    const rawData = tryRawJsonParse(response);
    if (rawData)
        return extractFromDict(rawData, "raw_json");
    // Strategy 4: Plaintext
    const plainResult = tryPlaintextParse(response);
    if (plainResult)
        return plainResult;
    return {
        score: 0,
        reasoning: "Failed to parse judge response: no parseable score found",
        dimensionScores: {},
    };
}
//# sourceMappingURL=parse.js.map