/**
 * A2-I Layer 2 — repo walker: gitignore cascade, extra excludes, extension filter,
 * size cap, hardcoded defaults, determinism (P-scanner-determinism).
 *
 * All fixtures are constructed in tmp dirs per-test so we don't fight with the
 * parent repo's own .gitignore (which blocks `.env`, `node_modules/`, etc. and
 * would otherwise cause fixture files to silently disappear from git).
 */
import { describe, test, expect } from "vitest";
import { join } from "node:path";
import { mkdtempSync, mkdirSync, writeFileSync, rmSync } from "node:fs";
import { tmpdir } from "node:os";
import { scanRepo } from "../../../../src/control-plane/instrument/scanner/walker.js";
import fc from "fast-check";

async function collectPaths(iter: AsyncIterable<{ readonly path: string }>): Promise<string[]> {
  const out: string[] = [];
  for await (const sf of iter) out.push(sf.path);
  return out;
}

/**
 * Build a simple multi-language, multi-directory fixture repo. Matches the
 * structure the spec's fixture description calls for:
 *   - src/app.py       (yielded)
 *   - src/client.ts    (yielded)
 *   - config/settings.json   (excluded by extension filter)
 *   - .env                   (hardcoded default)
 *   - node_modules/pkg/index.js   (hardcoded default)
 *   - .gitignore             (listing `ignored-by-gitignore.py` + `build/`)
 *   - ignored-by-gitignore.py   (.gitignore excluded)
 */
function buildSimpleRepo(): string {
  const tmp = mkdtempSync(join(tmpdir(), "autoctx-simple-"));
  mkdirSync(join(tmp, "src"));
  mkdirSync(join(tmp, "config"));
  mkdirSync(join(tmp, "node_modules", "pkg"), { recursive: true });
  writeFileSync(
    join(tmp, "src", "app.py"),
    "import os\nfrom openai import OpenAI\nclient = OpenAI()\n",
  );
  writeFileSync(
    join(tmp, "src", "client.ts"),
    `import { Anthropic } from "@anthropic-ai/sdk";\nconst c = new Anthropic();\n`,
  );
  writeFileSync(join(tmp, "config", "settings.json"), `{"x":1}\n`);
  writeFileSync(join(tmp, ".env"), "SECRET=v\n");
  writeFileSync(join(tmp, "node_modules", "pkg", "index.js"), "module.exports = {};\n");
  writeFileSync(join(tmp, "ignored-by-gitignore.py"), "print(1)\n");
  writeFileSync(join(tmp, ".gitignore"), "build/\n*.log\nignored-by-gitignore.py\n");
  return tmp;
}

describe("scanRepo — simple-repo fixture", () => {
  test("yields only supported source files, honoring defaults + .gitignore + extension filter", async () => {
    const cwd = buildSimpleRepo();
    try {
      const paths = await collectPaths(scanRepo({ cwd }));
      expect(paths.sort()).toEqual(["src/app.py", "src/client.ts"]);
    } finally {
      rmSync(cwd, { recursive: true, force: true });
    }
  });

  test("deterministic iteration order (alphabetical, DFS)", async () => {
    const cwd = buildSimpleRepo();
    try {
      const first = await collectPaths(scanRepo({ cwd }));
      const second = await collectPaths(scanRepo({ cwd }));
      expect(first).toEqual(second);
    } finally {
      rmSync(cwd, { recursive: true, force: true });
    }
  });
});

describe("scanRepo — extra excludes + exclude-from", () => {
  test("--exclude drops matching files", async () => {
    const cwd = buildSimpleRepo();
    try {
      const paths = await collectPaths(
        scanRepo({ cwd, extraExcludes: ["src/client.*"] }),
      );
      expect(paths).toEqual(["src/app.py"]);
    } finally {
      rmSync(cwd, { recursive: true, force: true });
    }
  });

  test("--exclude-from reads gitignore-style file + honors patterns", async () => {
    const cwd = buildSimpleRepo();
    const excludeTmp = mkdtempSync(join(tmpdir(), "autoctx-excludefrom-"));
    try {
      const excludeFile = join(excludeTmp, "excludes.txt");
      writeFileSync(excludeFile, "src/client.*\n");
      const paths = await collectPaths(scanRepo({ cwd, excludeFrom: excludeFile }));
      expect(paths).toEqual(["src/app.py"]);
    } finally {
      rmSync(cwd, { recursive: true, force: true });
      rmSync(excludeTmp, { recursive: true, force: true });
    }
  });
});

describe("scanRepo — size cap", () => {
  test("files over maxFileBytes are skipped with callback", async () => {
    const tmp = mkdtempSync(join(tmpdir(), "autoctx-sizecap-"));
    try {
      writeFileSync(join(tmp, "big.py"), "x = 1\n" + "# pad\n".repeat(100));
      writeFileSync(join(tmp, "small.py"), "y = 2\n");
      const skipped: string[] = [];
      const paths = await collectPaths(
        scanRepo({
          cwd: tmp,
          maxFileBytes: 50,
          onSkipOversized: (p) => skipped.push(p),
        }),
      );
      expect(paths).toEqual(["small.py"]);
      expect(skipped).toEqual(["big.py"]);
    } finally {
      rmSync(tmp, { recursive: true, force: true });
    }
  });
});

describe("scanRepo — hardcoded defaults are non-negotiable", () => {
  test(".env never yielded even if .gitignore does not list it", async () => {
    const tmp = mkdtempSync(join(tmpdir(), "autoctx-defaults-"));
    try {
      writeFileSync(join(tmp, ".env"), "K=v\n");
      mkdirSync(join(tmp, "node_modules", "pkg"), { recursive: true });
      writeFileSync(join(tmp, "node_modules", "pkg", "index.js"), "module.exports = {};");
      writeFileSync(join(tmp, "app.py"), "x=1\n");
      const paths = await collectPaths(scanRepo({ cwd: tmp }));
      expect(paths).toEqual(["app.py"]);
    } finally {
      rmSync(tmp, { recursive: true, force: true });
    }
  });

  test("keys and pem files skipped by default", async () => {
    const tmp = mkdtempSync(join(tmpdir(), "autoctx-keys-"));
    try {
      writeFileSync(join(tmp, "id.pem"), "-----BEGIN PRIVATE KEY-----\n");
      writeFileSync(join(tmp, "tls.key"), "K");
      writeFileSync(join(tmp, "app.py"), "x=1\n");
      const paths = await collectPaths(scanRepo({ cwd: tmp }));
      expect(paths).toEqual(["app.py"]);
    } finally {
      rmSync(tmp, { recursive: true, force: true });
    }
  });
});

describe("scanRepo — nested gitignore cascade", () => {
  test("nested .gitignore excludes files within its subtree", async () => {
    const tmp = mkdtempSync(join(tmpdir(), "autoctx-nested-"));
    try {
      mkdirSync(join(tmp, "pkg"));
      writeFileSync(join(tmp, ".gitignore"), "");
      writeFileSync(join(tmp, "pkg", ".gitignore"), "internal.py\n");
      writeFileSync(join(tmp, "pkg", "public.py"), "x=1\n");
      writeFileSync(join(tmp, "pkg", "internal.py"), "y=2\n");
      writeFileSync(join(tmp, "top.py"), "z=3\n");
      const paths = await collectPaths(scanRepo({ cwd: tmp }));
      expect(paths.sort()).toEqual(["pkg/public.py", "top.py"]);
    } finally {
      rmSync(tmp, { recursive: true, force: true });
    }
  });
});

describe("scanRepo — P-scanner-determinism property test", () => {
  test("same repo state → same file iteration order (property, 30 runs × 2 scans)", async () => {
    await fc.assert(
      fc.asyncProperty(
        fc.uniqueArray(
          fc.stringMatching(/^[a-z]{3,8}$/),
          { minLength: 3, maxLength: 8 },
        ),
        async (names) => {
          const tmp = mkdtempSync(join(tmpdir(), "autoctx-det-"));
          try {
            for (const n of names) {
              writeFileSync(join(tmp, `${n}.py`), `${n} = 1\n`);
            }
            const a = await collectPaths(scanRepo({ cwd: tmp }));
            const b = await collectPaths(scanRepo({ cwd: tmp }));
            return JSON.stringify(a) === JSON.stringify(b);
          } finally {
            rmSync(tmp, { recursive: true, force: true });
          }
        },
      ),
      { numRuns: 30 },
    );
  });
});
