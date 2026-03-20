/**
 * Elo rating functions — domain-agnostic scoring primitive (AC-343 Task 8).
 * Mirrors Python's autocontext/harness/scoring/elo.py.
 */

export function expectedScore(playerRating: number, opponentRating: number): number {
  return 1 / (1 + 10 ** ((opponentRating - playerRating) / 400));
}

export function updateElo(
  playerRating: number,
  opponentRating: number,
  actualScore: number,
  kFactor = 24.0,
): number {
  const expected = expectedScore(playerRating, opponentRating);
  return playerRating + kFactor * (actualScore - expected);
}
