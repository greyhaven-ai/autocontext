import { defineConfig } from "vitest/config";

export default defineConfig({
  test: {
    include: ["tests/**/*.test.ts"],
    maxWorkers: 4,
    // Many tests spawn the CLI cold (npx tsx src/cli/index.ts) rather than
    // against a pre-built dist/. That's ~1-2s per spawn on a fast dev
    // machine but can run ~5s+ per spawn on CI runner hardware, and several
    // tests chain 3-5 spawns. The vitest default (5000ms) is tuned for the
    // former and flakes on the latter; 30000ms gives CI headroom without
    // masking genuine hangs. Individual tests with a slower one-off step
    // (e.g. a real `npm run build`) still set their own larger override.
    testTimeout: 30000,
  },
});
