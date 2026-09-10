# Control-plane authentication

Autocontext authenticates HTTP and WebSocket control-plane traffic with scoped,
short-lived HMAC proofs. The signing secret stays on the client and server; it
is never the bearer value sent across the network.

This mechanism limits credential replay and lets the server give each key a
principal, capability ceiling, validity window, and disabled state. It does not
encrypt traffic or replace a TLS-terminating, authenticating, and authorizing
reverse proxy for a shared deployment.

## Configure credentials

For one trusted operator, set a random signing key of at least 32 bytes:

```bash
export AUTOCONTEXT_SERVER_TOKEN="$(openssl rand -hex 32)"
```

This compatibility setting creates key id `env`, principal `host-operator`,
with every server capability. The value is an HMAC key, not a bearer token.

For distinct principals or smaller capability ceilings, set
`AUTOCONTEXT_SERVER_CREDENTIALS_FILE` to a version 1 JSON registry:

```json
{
  "version": 1,
  "credentials": [
    {
      "kid": "operator-console",
      "principal": "deployment-operator",
      "secret": "replace-with-at-least-32-random-bytes",
      "capabilities": [
        "content:read",
        "control:operate",
        "control:read",
        "host:execute"
      ],
      "not_before": 1788134400,
      "not_after": 1790812800,
      "disabled": false
    }
  ]
}
```

The `capabilities` array must be nonempty, sorted, unique, and contain only
known capabilities. `not_before` and `not_after` are optional inclusive Unix
epoch seconds; `disabled` defaults to `false`. Secrets are UTF-8 strings from
32 through 4,096 bytes. A registry may contain at most 256 credentials and may
not exceed 64 KiB.

On POSIX, the registry path must be absolute and contain neither `..` nor
symlink components. Every parent directory must be owned by root or the server
user and must not be group/world writable. The path must resolve to the same
regular file before and after its descriptor is opened, that file must be owned
by the server user, and its mode must be exactly `0400` or `0600`. Autocontext
rejects an unsafe or malformed registry at startup. File registries are rejected
on Windows until equivalent owner and DACL validation is implemented; use
`AUTOCONTEXT_SERVER_TOKEN` there. Credentials are loaded at startup, so restart
the server after rotation or revocation. If the compatibility key and registry
are both configured, do not use the reserved key id `env` in the registry.

Provision each client with only its own key id and secret through a secret
manager or equivalently protected file. Do not distribute the server registry.

## Sign each request

A proof has three unpadded base64url components:

```text
actx1.<base64url(canonical-claims-json)>.<base64url(hmac-sha256)>
```

The HMAC input is the ASCII string `actx1.<claims-component>`. Claims use
compact, key-sorted UTF-8 JSON and contain exactly:

| Claim | Meaning |
| --- | --- |
| `v` | Integer `1` |
| `kid` | Server registry key id; 1–64 URL-safe characters |
| `iat` | Issued-at Unix epoch second |
| `exp` | Expiry Unix epoch second; no more than 60 seconds after `iat` |
| `jti` | Fresh 32-character lowercase hexadecimal identifier |
| `caps` | Sorted, unique, nonempty capabilities requested for this operation |
| `method` | Uppercase HTTP method; WebSocket upgrades use `GET` |
| `target` | Exact raw path plus query string, beginning with `/` |
| `origin` | Exact `Origin` header value, or an empty string when absent |
| `aud` | Literal `autocontext-control-plane` |

The server allows five seconds of clock skew. A requested capability must fit
within the server-side credential ceiling. Request only what the operation
needs; possessing a broader key does not add capabilities omitted from the
proof.

Generate a new proof for every HTTP attempt and every WebSocket reconnect. Send
an HTTP proof as:

```http
Authorization: Bearer actx1.<claims>.<signature>
```

Native WebSocket clients may use that header. Browser WebSocket clients offer
the exact `actx1.<claims>.<signature>` value as a subprotocol, which the server
echoes after successful authentication. Do not base64-wrap the proof again, put
it in URL userinfo, or add it to a query parameter.

WebSocket authentication covers the connection rather than each message. The
interactive handshake must therefore request every capability needed later on
that connection. The server closes the socket when the handshake proof expires;
clients must reconnect with a newly signed proof.

Trusted client code can use `build_control_plane_proof` from
`autocontext.server.auth` in Python, or `ServerCredentialSigner` from
`autoctx/server/auth` in TypeScript. The built-in TypeScript TUI signs every
HTTP attempt and WebSocket reconnect automatically when
`AUTOCONTEXT_SERVER_TOKEN` is configured.

## Capabilities and routes

| Capability | Authority |
| --- | --- |
| `control:read` | Read control-plane state; required by HTTP `GET`/`HEAD` and `/ws/events` |
| `control:operate` | Mutate or operate control-plane state; required by non-read HTTP methods, legacy GET routes that persist artifacts or synchronize/repair status, and `/ws/interactive` |
| `control:admin` | Login, logout, and provider administration; implies `control:read` and `control:operate`, but never content access or host execution |
| `content:read` | Read content-bearing data; additionally required by `/api/*`, `/ws/events`, and `/ws/interactive` |
| `host:execute` | Trigger host/provider execution, including run/scenario generation, agent chat, dynamic scenario discovery/manifests, executable knowledge import/search/export, solve, consultation, evaluation, simulation, mission, campaign, and distillation operations |

An interactive connection requests `content:read` plus `control:operate` at
handshake. Commands that run agents, providers, generated scenarios, or host
tools additionally require `host:execute`; login, logout, and provider changes
require `control:admin`. Resuming work or overriding a gate also requires
`host:execute`; cancellation, pausing, hints, and other non-executing run-control
commands remain `control:operate` operations.

The Python `/health` endpoint is public. HTTP `OPTIONS` preflight is proof
exempt, but Origin/CORS policy still applies. All other routes fail closed when
the proof is missing, expired, replayed, malformed, signed by an unknown or
disabled key, bound to a different request, or outside the key's capability
ceiling.

## Replay and deployment boundaries

Each server process atomically records accepted `(kid, jti)` pairs in a bounded
cache until the proof expires. A duplicate is rejected, and cache exhaustion
fails closed. The cache is process-local: run a single control-plane process,
or provide equivalent shared atomic replay protection before load-balancing the
same credential across multiple processes. Restarting a process clears its
cache, another reason TLS and short proof lifetimes remain necessary.

Validate browser Origin before proof verification and preserve the exact raw
path and query through proxies. Terminate TLS before any network-visible hop;
an intercepted proof can otherwise be used by an attacker during its short
validity window if the legitimate request has not consumed it yet.

For an explicitly accepted local-development exception in either runtime, set
`AUTOCONTEXT_ALLOW_TOKENLESS_LOOPBACK=1`. It accepts only a peer observed on a
loopback transport. A loopback reverse proxy is therefore indistinguishable
from a direct local client: never put this mode behind any proxy, port forward,
or tunnel. Both runtimes reject configured external browser origins while
tokenless mode is active, but an originless native proxy still cannot be
distinguished. Do not use it on a persistent or shared host. Python test
fixtures may opt into an in-process compatibility principal with
`create_app(allow_insecure_test_principal=True)`; the production default never
infers authority from the peer name `testclient`.

## Migration from raw bearer tokens

`AUTOCONTEXT_SERVER_TOKEN` remains accepted as configuration, but its wire
meaning changed. Sending its raw value in `Authorization`, reusing one proof,
or using the legacy wrapped WebSocket subprotocol now fails authentication.
Upgrade the server and clients together. Clients must sign the exact method,
target, Origin, audience, and required capabilities for every attempt, and must
generate a new JTI after redirects, retries, or reconnects.
