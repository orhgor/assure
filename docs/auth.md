# Accounts: local user management (2026-09-27)

One EC2 per company, no SSO. Identity therefore lives in PostgreSQL beside the
data it protects: `auth_users`, `auth_invitations`, `auth_sessions`,
`auth_audit_events`, `org_settings` (schema v35, `db/connection._migrate_v35`;
repository `db/auth_repository.py`; roles and the guard `rbac.py`; routes
`routers/auth_routes.py`, `routers/team_routes.py`).

## Modes

`ASSURE_AUTH_MODE` = `local` | `clerk` | `off`.

| Unset → resolves to | When |
|---|---|
| `local` | `ASSURE_BOOTSTRAP_TOKEN` is set **or** an `auth_users` row exists |
| `clerk` | Clerk keys configured and no local users |
| `off` | otherwise (laptop, tests: `create_app(require_auth=False)` with no token) |

In `local` mode a `before_request` guard (`rbac.register_auth_guard`, registered
in `web.py` next to `register_security_guards`) resolves the signed cookie's
`auth_session_id` into `g.assure_user = {id, email, display_name, role,
permissions, must_change_password}` and answers **401
`{"ok": false, "error": "sign in required"}`** for every `/api/*` path that is
not public. Public: `/health`, `/api/health`, `/ready`, `/api/auth/setup-status`,
`/api/auth/setup`, `/api/auth/login`, `/api/auth/accept-invitation`,
`/api/auth/config`, `/api/auth/me` (signed-out shape), `/api/i18n`, `/static/*`.
Server pages under `/parsing` redirect to `/signin?next=…` when signed out, so
the app on :8765 is safe on its own, not only behind the gate. The gate's
`SHELL_ACCESS_KEY` stays as the fallback for the static shell.

In `off` and `clerk` modes the `requires(...)` decorator is a no-op (every
caller is owner-equivalent) — nothing that existed before this feature changes.
When Clerk keys are left in `.env` on a box that has local users, local wins and
the Clerk `before_request` steps aside.

## Roles and permissions

`rbac.ROLES = owner, compliance_reviewer, reviewer, intake, auditor`.

| Permission | owner | compliance_reviewer | reviewer | intake | auditor |
|---|---|---|---|---|---|
| projects.read | x | x | x | x | x |
| projects.create | x | x | x | | |
| documents.upload | x | x | x | x | |
| documents.delete | x | x | | | |
| documents.download_original | x | x | | | x |
| fields.accept | x | x | x | | |
| fields.accept_compliance | x | x | | | |
| fields.correct | x | x | x | | |
| fields.dispute | x | x | x | | |
| disputes.resolve | x | x | | | |
| classification.override | x | x | x | | |
| reports.replay | x | x | | | |
| exports.read | x | x | x | | x |
| exports.dossier | x | x | | | x |
| settings.integrations | x | | | | |
| settings.schemas | x | | | | |
| settings.models | x | | | | |
| team.manage | x | | | | |
| audit.read | x | x | | | x |
| compile.run | x | x | x | | |
| sources.manage | x | x | x | x | |

Denied: **403 `{"ok": false, "error": "not allowed: <perm>", "required":
[...], "role": "<role>"}`**. Where each permission bites:

| Permission | Routes |
|---|---|
| fields.accept (+ fields.accept_compliance when the field is `compliance_bound`) | `POST …/parsure/<r>/fields/<f>/accept` |
| fields.correct / fields.dispute / disputes.resolve | `…/correct`, `…/dispute`, `…/disputes/<d>/resolve` |
| classification.override / reports.replay | `…/classification`, `…/replay` |
| exports.read | `GET …/parsure/export`, `GET …/parsure/<r>/export`, `GET /api/projects/<p>/export` (document formats) |
| exports.dossier | `GET /api/projects/<p>/export?format=dossier-pdf|audit-pdf|bundle` (on top of exports.read) |
| documents.upload | `POST …/import-pdf`, `…/substrate/upload`, `…/jdf/ingest` |
| documents.delete | `DELETE …/substrate/<id>` |
| documents.download_original | `GET …/documents/<id>/original` |
| settings.integrations | `PUT`/`DELETE /api/integrations/aws` (the status `GET` stays readable to every signed-in user so the Sources panel can show the S3 state) |
| compile.run | `…/draft/stream`, `…/draft/redhat/stream`, `…/inquire/stream` |
| team.manage | every `/api/team/*` route |
| audit.read | `GET /api/audit`, `GET /api/team/users/<id>/activity` |

`GET /api/parsure/schemas` needs a sign-in but no particular role; there are no
schema-writing routes yet (`settings.schemas` is reserved). `projects.read`,
`projects.create`, `sources.manage` and `settings.models` are defined in the
matrix but not yet attached to a route.

## First run

`scripts/gen-env.sh ec2` writes `ASSURE_AUTH_MODE=local`, generates
`ASSURE_BOOTSTRAP_TOKEN` and prints it. Opening the app shows the owner screen
(`GET /api/auth/setup-status` → `needs_owner: true`). `POST /api/auth/setup
{bootstrap_token, org_name, email, display_name, password}` compares the token
in constant time, creates the **owner**, records `org_settings.org_name` and
`setup_completed_at`, signs the owner in and answers `{ok, user}`. It is refused
with 403 once any account exists (a second owner comes from an invitation) or
when the token does not match, and with 400 when the password is under 12
characters (the rule is stated in words).

## Sign-in, sessions

`POST /api/auth/login {email, password}` → `{ok, user:{id, email, display_name,
role, permissions, must_change_password}}`. Wrong address or password: 401 with
one generic message. Disabled account: 403. Five failures in a row lock the
account for 15 minutes: 423 with `locked_until`, and the account's open sessions
are revoked. A successful sign-in resets the counter.

A sign-in creates an `auth_sessions` row (`ses-…`, ip, user agent) and stores
its id in the signed, permanent Flask session; lifetime `ASSURE_SESSION_HOURS`
(default 12), idle: every request slides `last_seen_at` and `expires_at`.
`POST /api/auth/logout` revokes the row. Owners see and revoke sessions at
`GET`/`DELETE /api/team/sessions[/<id>]`. Disabling a user, resetting a password
or a lockout revokes that user's sessions; changing one's own password revokes
the other ones.

`GET /api/auth/me` signed in: `{ok, user_id, email, display_name, role,
permissions, mode, must_change_password}`; signed out: `{ok, user_id: "", mode,
needs_owner}` (the gate reads `user_id`). `POST /api/auth/password
{current_password, new_password}` clears `must_change_password`.

## Invitations

`POST /api/team/invitations {email, role}` (owner) creates an `invited` account
and answers `{ok, invitation:{id, email, role, expires_at}, token, accept_url:
"/accept?token=…", emailed}`. The raw token appears in this response **once**;
the row keeps only its SHA-256. When `RESEND_API_KEY` is configured the link
(`ASSURE_PUBLIC_URL` + `accept_url`) is mailed through Resend; without it the
request still succeeds with `emailed: false`. Links are valid 7 days; an address
with an active or disabled account cannot be invited (409); re-inviting an
`invited` address replaces the previous link. `GET /api/team/invitations`,
`DELETE /api/team/invitations/<id>` (removes the placeholder account too).

`POST /api/auth/accept-invitation {token, display_name, password}` (public)
activates the account, signs it in, spends the token (404 afterwards or when
expired).

## Team

`GET /api/team/users` → `{ok, users:[{id, email, display_name, role, status,
must_change_password, last_login_at, created_at}]}`. `PATCH /api/team/users/<id>
{role?, status?, display_name?}` — 409 when it would change your own role or
demote/disable the last active owner; `status` is `active` or `disabled` (an
invited user becomes active by accepting). `POST /api/team/users/<id>/reset-password`
→ `{ok, temporary_password, must_change_password: true}`; the temporary
password is shown once and the user must change it at the next sign-in.

## Audit

Every review event (`parsure_audit_events`) now carries `actor_id`,
`actor_role` and `ip` beside the free-text `actor` (`db/parsure_repository.log_event`
reads `g.assure_user`; the actor text falls back to the account's display name
or e-mail when the body names none). Account events go to `auth_audit_events`:
`setup, login, login_failed, locked, logout, invited, invitation_revoked,
invitation_accepted, role_changed, status_changed, display_name_changed,
password_reset, password_changed, session_revoked`, each with actor, subject,
ip, user agent and a payload that names what changed (never a secret).

`GET /api/audit?project_id=&actor_id=&event_type=&since=&limit=` merges both
sources newest first: `{ok, events:[{id, source: parsure|auth, event_type,
project_id, report_id, field_name, actor, actor_id, actor_role, ip, payload,
created_at}]}` (auth rows add `subject_user_id` and `subject`). A `project_id`
filter yields review events only. `GET /api/team/users/<id>/activity` is the
same feed for one account (what they did and what was done to them).

`ip` is the first `X-Forwarded-For` hop when `PROXY_FIX_HOPS` ≥ 1 (the compose
default), else the socket peer. The shell gate on the same box does not add
`X-Forwarded-For` today, so rows written through it name `127.0.0.1` until it
does.

## What is never stored

- Plaintext passwords — only `werkzeug.security` scrypt hashes.
- Invitation tokens — only SHA-256; the token is in the creation response once.
- Temporary passwords — hashed like any other, returned once.
- Bootstrap token — read from the environment, compared in constant time, never
  written to a table or an audit payload.
