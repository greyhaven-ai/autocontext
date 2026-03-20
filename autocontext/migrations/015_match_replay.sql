-- AC-171: Add replay/state columns to matches for training export
ALTER TABLE matches ADD COLUMN winner TEXT NOT NULL DEFAULT '';
ALTER TABLE matches ADD COLUMN strategy_json TEXT NOT NULL DEFAULT '';
ALTER TABLE matches ADD COLUMN replay_json TEXT NOT NULL DEFAULT '';
