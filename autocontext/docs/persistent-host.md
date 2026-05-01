# Persistent Host Worker

Use this shape when you want Autocontext to keep accepting queued work while a server stays online.

Run the HTTP API and queue worker as separate long-lived processes against the same durable workspace:

```bash
export AUTOCONTEXT_DB_PATH=/srv/autoctx/runs/autocontext.sqlite3
export AUTOCONTEXT_RUNS_ROOT=/srv/autoctx/runs
export AUTOCONTEXT_KNOWLEDGE_ROOT=/srv/autoctx/knowledge

uv run autoctx serve --host 0.0.0.0 --port 8000
uv run autoctx worker --poll-interval 5 --concurrency 2
```

For the TypeScript package, the equivalent worker surface is:

```bash
autoctx serve --host 0.0.0.0 --port 8000
autoctx worker --poll-interval 5 --concurrency 2
```

`autoctx queue` and MCP `queue_task` calls write into the task queue. `autoctx worker` wraps the existing `TaskRunner`, polls that queue, processes tasks in priority order, and persists task results back into the configured store.

## Durable State

Keep these paths on persistent storage:

- `runs/`: run records, event streams, task queue SQLite DB, and per-run artifacts
- `knowledge/`: playbooks, hints, custom scenarios, and reusable context
- `skills/` and `.claude/skills/`: optional exported skills when those surfaces are enabled

SQLite is the current open-source queue store. Treat the DB path as a single-writer operational boundary unless you explicitly deploy with a storage adapter that provides stronger multi-worker semantics. A Postgres-backed queue can fit the same `serve + worker` shape, but should be introduced behind the storage abstraction rather than by changing task-runner behavior.

## Operational Notes

Use `--once` for cron, smoke tests, or CI:

```bash
uv run autoctx worker --once --concurrency 4 --json
```

Use `--max-empty-polls` for bounded workers that should exit after the queue drains:

```bash
uv run autoctx worker --poll-interval 1 --max-empty-polls 3
```

On a service manager such as systemd, run `serve` and `worker` as separate units with the same environment file. Restarting the worker should not require restarting the API process as long as both use the same durable paths.

### systemd Sketch

Use one environment file for both units:

```ini
# /etc/autoctx/autoctx.env
AUTOCONTEXT_DB_PATH=/srv/autoctx/runs/autocontext.sqlite3
AUTOCONTEXT_RUNS_ROOT=/srv/autoctx/runs
AUTOCONTEXT_KNOWLEDGE_ROOT=/srv/autoctx/knowledge
AUTOCONTEXT_AGENT_PROVIDER=pi
AUTOCONTEXT_PI_COMMAND=pi
```

```ini
# /etc/systemd/system/autoctx-serve.service
[Unit]
Description=Autocontext HTTP API
After=network-online.target

[Service]
WorkingDirectory=/srv/autoctx/app/autocontext
EnvironmentFile=/etc/autoctx/autoctx.env
ExecStart=/usr/bin/uv run autoctx serve --host 0.0.0.0 --port 8000
Restart=on-failure

[Install]
WantedBy=multi-user.target
```

```ini
# /etc/systemd/system/autoctx-worker.service
[Unit]
Description=Autocontext queue worker
After=network-online.target autoctx-serve.service

[Service]
WorkingDirectory=/srv/autoctx/app/autocontext
EnvironmentFile=/etc/autoctx/autoctx.env
ExecStart=/usr/bin/uv run autoctx worker --poll-interval 5 --concurrency 2
Restart=on-failure

[Install]
WantedBy=multi-user.target
```

### Container Sketch

Keep the same image for API and worker, but run separate processes and mount the same durable volume:

```yaml
services:
  autoctx-serve:
    image: ghcr.io/your-org/autoctx:latest
    command: ["uv", "run", "autoctx", "serve", "--host", "0.0.0.0", "--port", "8000"]
    env_file: .env.autoctx
    volumes:
      - autoctx-state:/srv/autoctx
    ports:
      - "8000:8000"

  autoctx-worker:
    image: ghcr.io/your-org/autoctx:latest
    command: ["uv", "run", "autoctx", "worker", "--poll-interval", "5", "--concurrency", "2"]
    env_file: .env.autoctx
    volumes:
      - autoctx-state:/srv/autoctx

volumes:
  autoctx-state:
```

The image build, reverse proxy, auth, TLS, and secret distribution are deployment-specific. Do not bake provider API keys into images.

## Storage Adapter Contract

The OSS worker now depends on a narrow task queue contract instead of SQLite inheritance:

- Python: `autocontext.execution.TaskQueueStore` / `TaskQueueEnqueueStore`
- TypeScript: `TaskQueueWorkerStore` / `TaskQueueEnqueueStore`

Adapters must provide atomic task claim, task lookup, completion, failure, and enqueue semantics. SQLite is the bundled implementation. A hosted Postgres adapter can add leases, heartbeats, retries, and multi-worker coordination behind that contract without changing `TaskRunner`.

What should stay outside the OSS contract is the hosted control plane: tenant scheduling, billing, policy UI, fleet routing, secret brokering, and managed retention/audit workflows.

## Sandbox Boundary

The worker uses the same evaluator/executor settings as the rest of Autocontext. Use Monty when you need in-process interpreter guardrails, PrimeIntellect or SSH when you need an external execution host, and reserve Gondolin for the optional microVM backend once it is wired. `AUTOCONTEXT_EXECUTOR_MODE=gondolin` is intentionally fail-closed today so deployments do not silently fall back to local execution when they expected a VM isolation boundary.

For future Gondolin work, implement the public backend contracts rather than changing task-runner behavior:

- Python: `autocontext.execution.executors.gondolin_contract.GondolinBackend`
- TypeScript: `GondolinBackend` and `createDefaultGondolinSandboxPolicy` from `autoctx`
