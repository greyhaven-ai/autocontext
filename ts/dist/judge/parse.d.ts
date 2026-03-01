/**
 * Multi-strategy judge response parser.
 *
 * Strategies (tried in order):
 * 1. Marker-based: <!-- JUDGE_RESULT_START/END -->
 * 2. Code block: ```json ... ```
 * 3. Raw JSON: { "score": ... } anywhere in text
 * 4. Plain text: "Score: 0.85" patterns
 */
export interface ParsedJudge {
    score: number;
    reasoning: string;
    dimensionScores: Record<string, number>;
}
export declare function parseJudgeResponse(response: string): ParsedJudge;
//# sourceMappingURL=parse.d.ts.map