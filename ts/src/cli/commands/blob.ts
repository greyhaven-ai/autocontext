/**
 * `blob` command (AC-853 split of command-handlers.ts).
 */
import { errorMessage } from "./shared.js";

// ---------------------------------------------------------------------------
// blob command (AC-518 Phase 4)
// ---------------------------------------------------------------------------

export async function cmdBlob(): Promise<void> {
  const { parseArgs } = await import("node:util");
  const { resolve } = await import("node:path");
  const { loadSettings } = await import("../../config/index.js");
  const {
    BLOB_HELP_TEXT,
    executeBlobHydrateWorkflow,
    executeBlobStatusWorkflow,
    executeBlobSyncWorkflow,
    getBlobSubcommand,
  } = await import("../blob-command-workflow.js");

  const subcommandPlan = getBlobSubcommand(process.argv[3]);

  if (subcommandPlan.kind === "help") {
    console.log(BLOB_HELP_TEXT);
    process.exit(0);
  }

  const subcommand = subcommandPlan.subcommand;

  const settings = loadSettings();

  if (!settings.blobStoreEnabled) {
    console.error("Error: blob store is not enabled. Set AUTOCONTEXT_BLOB_STORE_ENABLED=true");
    process.exit(1);
  }

  const { createBlobStore } = await import("../../blobstore/factory.js");
  const store = createBlobStore({
    backend: settings.blobStoreBackend ?? "local",
    root: resolve(settings.blobStoreRoot ?? "./blobs"),
  });

  switch (subcommand) {
    case "status": {
      const { values } = parseArgs({
        args: process.argv.slice(4),
        options: { json: { type: "boolean" } },
      });
      const { SyncManager } = await import("../../blobstore/sync.js");
      console.log(
        executeBlobStatusWorkflow({
          json: !!values.json,
          createSyncManager: () => new SyncManager(store, resolve(settings.runsRoot)),
        }),
      );
      break;
    }
    case "sync": {
      const { values } = parseArgs({
        args: process.argv.slice(4),
        options: {
          "run-id": { type: "string" },
          json: { type: "boolean" },
        },
      });
      try {
        const { SyncManager } = await import("../../blobstore/sync.js");
        const result = executeBlobSyncWorkflow({
          runId: values["run-id"],
          json: !!values.json,
          createSyncManager: () => new SyncManager(store, resolve(settings.runsRoot)),
        });
        if (result.stderrLines) {
          for (const line of result.stderrLines) console.error(line);
        }
        console.log(result.stdout);
      } catch (error) {
        console.error(errorMessage(error));
        process.exit(1);
      }
      break;
    }
    case "hydrate": {
      const { values } = parseArgs({
        args: process.argv.slice(4),
        options: {
          key: { type: "string" },
          output: { type: "string", short: "o" },
        },
      });
      try {
        const { writeFileSync, mkdirSync } = await import("node:fs");
        const { dirname } = await import("node:path");
        const result = executeBlobHydrateWorkflow({
          key: values.key,
          output: values.output,
          store,
          writeOutputFile: (outputPath, data) => {
            mkdirSync(dirname(resolve(outputPath)), { recursive: true });
            writeFileSync(resolve(outputPath), data);
          },
        });
        if (result.stdoutBuffer) {
          process.stdout.write(result.stdoutBuffer);
        } else if (result.stdout) {
          console.log(result.stdout);
        }
      } catch (error) {
        console.error(errorMessage(error));
        process.exit(1);
      }
      break;
    }
    default:
      console.error(`Unknown blob subcommand: ${subcommand}. Run 'autoctx blob --help'`);
      process.exit(1);
  }
}
