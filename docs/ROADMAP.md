# RustDesk Panel — roadmap

## Phase 0 — Repository and safety
- [x] Create private GitHub repository and baseline documentation.
- [x] Add rules preventing secrets, databases, and runtime state from being committed.
- [ ] Import audited MVP 0.1 source files.
- [ ] Add CI: unit tests, lint, dependency checks, Docker build.
- [ ] Add configuration templates without environment-specific passwords.

## Phase 1 — Local device inventory (MVP)
- [ ] Read HBBS metadata with least-privilege read-only export.
- [ ] Validate compatibility of actual HBBS SQLite schema (not assumed).
- [ ] Web login with password hashing, session expiry, CSRF protection, basic rate limiting.
- [ ] Device list with groups, aliases, notes, filtering and manual registration.
- [ ] Safe Docker Compose deployment alongside existing HBBS/HBBR.
- [ ] Backup/restore for the panel's own database.

## Phase 2 — Administration
- [ ] Users and access roles inside the panel, 2FA.
- [ ] Audit administrative actions.
- [ ] Device last-seen / online state only if backed by reliable heartbeat data.
- [ ] Secure invitation/onboarding flow.
- [ ] Version inventory and configuration management with authenticated agent or client API.

## Phase 3 — RustDesk integration
- [ ] Evaluate address-book/API compatibility with supported RustDesk clients.
- [ ] Shared contacts and groups where client protocol supports it.
- [ ] Access enforcement design at server/client layer — UI roles alone are not enforcement.
- [ ] Session accounting from a verifiable event source, without inventing event records.

## Invariants
- Existing `HBBS` and `HBBR` keep working even if the panel fails.
- Do not rewrite RustDesk's SQLite DB or modify keys.
- Do not publish ports of the admin panel outside a trusted LAN/VPN before full security review.
- All real-world NAS data, IP-dependent config and passwords remain outside Git.
