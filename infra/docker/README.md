# Container security

The Compose configuration is intended for local evaluation. It publishes the
dashboard only on `127.0.0.1`, requires an explicit server token, runs as UID
10001, drops all Linux capabilities, enables `no-new-privileges`, limits PIDs,
and keeps the container root filesystem read-only. The declared `runs`,
`knowledge`, and `skills` named volumes are initialized with the image's
UID/GID 10001 ownership and are the only writable persistent paths. Named
volumes avoid making a fixed container UID depend on checkout-directory
ownership; use `docker compose cp` or an explicit backup container when you
need to export their contents.

Generate a unique token of at least 32 random characters and pass it through
the environment before starting the dashboard:

```console
AUTOCONTEXT_SERVER_TOKEN="$(openssl rand -hex 32)" docker compose -f infra/docker/docker-compose.yml up dashboard
```

For a remote deployment, do not publish the application port directly. Keep
the application on a private network behind a TLS-terminating reverse proxy,
store `AUTOCONTEXT_SERVER_TOKEN` in the platform's secret manager, restrict
ingress to intended operators, and rotate the token after suspected exposure.
Set platform CPU and memory quotas for the workload. The bearer token protects
the control plane but does not turn local candidate execution into a tenant
isolation boundary; mutually untrusted workloads still require a dedicated
container, sandbox, or microVM per tenant.
