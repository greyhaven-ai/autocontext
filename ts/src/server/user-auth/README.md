# Engine user auth

The engine can verify a user identity JWT that the desktop client attaches, and enforce it on run-affecting operations. It is issuer-agnostic (verifies any JWT against a configured JWKS URL), so it works with a company IdP directly without any autocontext-specific adapter.

## Configuration

- `AUTOCONTEXT_AUTH_JWKS_URL`: the IdP JWKS endpoint.
- `AUTOCONTEXT_AUTH_ISSUER`: the expected token issuer.
- `AUTOCONTEXT_AUTH_AUDIENCE`: optional expected audience.

Auth is DISABLED (no enforcement, the default local-mode behavior) unless both the JWKS URL and issuer are set.

## When enabled

- **WebSocket**: the client sends `{ "type": "authenticate", "token": "..." }`; run-affecting commands (start_run, create_scenario, and similar) require a verified session. Read-only and provider-auth commands (list_scenarios, whoami, switch_provider) are always open.
- **HTTP**: mutating requests (POST/PUT/PATCH/DELETE) require an `Authorization: Bearer <token>` header; GET is open.

## Notes

Gating is binary (verified or not); role/group-based permissions (RBAC) and run audit are follow-on work. The token is verified with `jose` against the JWKS (signature, issuer, audience, expiry).
