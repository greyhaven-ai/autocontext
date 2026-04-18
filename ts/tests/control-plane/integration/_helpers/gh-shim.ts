// Shared helper for Layer-10 integration tests that need to intercept the
// `gh` and (selectively) `git` CLI calls.
//
// The shim works by writing two bash scripts (`gh` and `git`) into a tmp dir
// and prepending that dir to PATH for the test process via the env returned
// by `installGhShim()`. The `gh` shim records every invocation to a JSONL log
// and prints a stub PR URL on `gh pr create`. The `git` shim ALSO records
// every invocation but only intercepts `git push` (which would otherwise
// fail without a real remote); all other git verbs delegate to the real git
// binary so branch-creation, add, and commit still work.

import { mkdtempSync, rmSync, writeFileSync, chmodSync } from "node:fs";
import { tmpdir } from "node:os";
import { delimiter, join } from "node:path";

export interface GhShim {
  /** Directory containing the bash shim scripts; prepend to PATH. */
  readonly dir: string;
  /** JSONL file recording every `gh` and `git push` invocation. */
  readonly logPath: string;
  /** PR URL that the `gh pr create` shim prints on stdout. */
  readonly prUrl: string;
  /**
   * Build a process env that has the shim dir prepended to PATH and
   * git-config isolation set against `repoCwd`.
   */
  env(repoCwd: string): NodeJS.ProcessEnv;
  /** Tear down the shim dir. */
  cleanup(): void;
}

const SHIM_LOG_HELPER = `
log_args() {
  local j="["
  local first=1
  for a in "$@"; do
    local esc="\${a//\\\\/\\\\\\\\}"
    esc="\${esc//\\"/\\\\\\"}"
    if [ $first -eq 1 ]; then
      j="$j\\"$esc\\""
      first=0
    else
      j="$j,\\"$esc\\""
    fi
  done
  j="$j]"
  printf '%s\\n' "$j" >> "$LOG"
}
`;

export interface InstallGhShimOptions {
  /** Override the stub PR URL the `gh` shim prints. */
  readonly prUrl?: string;
}

/**
 * Create the shim dir + the two bash scripts. Returns a handle the caller
 * uses to build the test env and tear down on completion.
 */
export function installGhShim(opts: InstallGhShimOptions = {}): GhShim {
  const dir = mkdtempSync(join(tmpdir(), "autocontext-gh-shim-"));
  const logPath = join(dir, "invocations.jsonl");
  const prUrl = opts.prUrl ?? "https://github.com/example/repo/pull/42";

  writeShim(
    dir,
    "gh",
    `set -e
LOG="${logPath}"
${SHIM_LOG_HELPER}
log_args "$@"
case "$1" in
  auth)
    echo "logged in"
    exit 0
    ;;
  pr)
    shift
    case "$1" in
      create)
        echo "${prUrl}"
        exit 0
        ;;
    esac
    ;;
  --version)
    echo "gh shim 0.0.0"
    exit 0
    ;;
esac
exit 0
`,
  );

  writeShim(
    dir,
    "git",
    `set -e
LOG="${logPath}"
${SHIM_LOG_HELPER}
if [ "$1" = "push" ]; then
  log_args "$@"
  echo "pushed (shim)"
  exit 0
fi
REAL_GIT=""
for candidate in /usr/bin/git /usr/local/bin/git /opt/homebrew/bin/git; do
  if [ -x "$candidate" ]; then
    REAL_GIT="$candidate"
    break
  fi
done
if [ -z "$REAL_GIT" ]; then
  echo "git shim: no real git found" >&2
  exit 127
fi
exec "$REAL_GIT" "$@"
`,
  );

  return {
    dir,
    logPath,
    prUrl,
    env(repoCwd: string): NodeJS.ProcessEnv {
      const basePath = `${dir}${delimiter}${process.env.PATH ?? ""}`;
      return {
        ...process.env,
        HOME: repoCwd,
        GIT_CONFIG_GLOBAL: join(repoCwd, ".gitconfig-test"),
        GIT_CONFIG_SYSTEM: "/dev/null",
        GIT_AUTHOR_NAME: "Test Author",
        GIT_AUTHOR_EMAIL: "test@example.com",
        GIT_COMMITTER_NAME: "Test Author",
        GIT_COMMITTER_EMAIL: "test@example.com",
        PATH: basePath,
      };
    },
    cleanup(): void {
      rmSync(dir, { recursive: true, force: true });
    },
  };
}

function writeShim(dir: string, name: string, body: string): void {
  const p = join(dir, name);
  writeFileSync(p, `#!/usr/bin/env bash\n${body}\n`, "utf-8");
  chmodSync(p, 0o755);
}
