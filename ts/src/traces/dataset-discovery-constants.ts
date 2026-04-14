export const CONVENTIONAL_DATASET_DIRECTORIES = [
  "data",
  "fixtures",
  "benchmarks",
  "examples",
  "training_data",
  "datasets",
] as const;

export const DATASET_FILE_EXTENSIONS = new Set([".jsonl", ".json", ".csv", ".md"]);

export const IGNORED_DATASET_FILENAMES = new Set([
  "package.json",
  "tsconfig.json",
  "package-lock.json",
  ".autoctx-data.json",
]);
