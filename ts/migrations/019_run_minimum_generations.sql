-- Persist the effective minimum iteration floor beside the run ceiling.
ALTER TABLE runs
ADD COLUMN minimum_generations INTEGER NOT NULL DEFAULT 1
CHECK (minimum_generations >= 1);
