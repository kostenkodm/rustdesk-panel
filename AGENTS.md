# AGENTS.md — development contract

Project: **RustDesk Panel**. Independent management console for RustDesk Server OSS.

## Non-negotiable safety rules
1. **Never** stop, delete, reconfigure, restart or replace existing `HBBS` / `HBBR` containers as part of panel development or migrations.
2. **Never** commit files from a real `/volume*/docker/rustdesk*` directory, key material, SQLite DBs, server tokens, passwords or real device inventories.
3. Read HBBS data using a restricted read-only export. All reads must be resilient to SQLite locking/schema differences. Never write to HBBS SQLite.
4. Never claim real-time online status, session history, server-side access control or client address-book integration without a functioning verified data source and integration tests.
5. The panel must operate as a separately managed Compose project and fail independently of RustDesk.
6. Keep the admin UI LAN/VPN-only by default. No unsafe `0.0.0.0` publication to the internet.
7. Validate user input and authorization on every state-changing endpoint. No secrets in logs; defend against CSRF, XSS and brute-force attempts.
8. Store sensitive deployment settings outside version control. Provide safe `.env.example` if configuration is required.

## Workflow
- Make changes on feature branches; use PRs into `main`.
- Supply tests and installation/rollback notes with each functional change.
- Do not assume the user NAS path is a universal default; use configurable mount paths.
- Start with a working, testable MVP before adding integrations or rewriting RustDesk.
- Keep third-party licenses and attribution intact.
