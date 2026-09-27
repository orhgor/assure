"""Local accounts: users, invitations, sessions, org settings, account audit.

Tables from ``db/connection._migrate_v35`` (2026-09-27). The product runs on
one machine per company with no SSO, so the identity store is these rows in
PostgreSQL. What is never stored: a password (only the werkzeug scrypt hash),
an invitation token (only its SHA-256), a temporary password (returned once
to the owner who asked for it). Every write here is plain SQL in the portable
dialect ``pg_compat`` translates; timestamps are microsecond text
(``%Y-%m-%d %H:%M:%S.%f``) as in ``parsure_repository`` so two events inside
one second still order (the v35 columns are ``TIMESTAMP(6)``).

The roles themselves — which permissions each grants — are ``rbac.py``'s;
this module only stores the role name and refuses one it does not know.
"""

from __future__ import annotations

import hashlib
import json
import secrets
from datetime import datetime, timedelta, timezone
from typing import Any

from werkzeug.security import check_password_hash, generate_password_hash

try:
    from ..db.connection import init_db
    from ..history import get_db
    from ..rbac import ROLES
except ImportError:
    from db.connection import init_db
    from history import get_db
    from rbac import ROLES

_TS = "%Y-%m-%d %H:%M:%S.%f"

USER_STATUSES = ("active", "disabled", "invited")

#: Sign-in lockout (docs/auth.md): the fifth wrong password in a row locks the
#: account for fifteen minutes. Counted per account, not per address, because
#: the box sits behind one gate and every request arrives from it.
LOCKOUT_THRESHOLD = 5
LOCKOUT_MINUTES = 15

#: An invitation link is good for seven days; the accepting user chooses the
#: password, so nothing secret travels in the e-mail except the one-time token.
INVITATION_DAYS = 7

#: The shortest password the policy accepts (the setup and change routes
#: state this rule in words). Length, not character classes: NIST 800-63B.
MIN_PASSWORD_LENGTH = 12

AUTH_EVENT_TYPES = (
    "setup",
    "login",
    "login_failed",
    "locked",
    "logout",
    "invited",
    "invitation_revoked",
    "invitation_accepted",
    "role_changed",
    "status_changed",
    "display_name_changed",
    "password_reset",
    "password_changed",
    "session_revoked",
)


def _now_dt() -> datetime:
    return datetime.now(timezone.utc)


def _now() -> str:
    return _now_dt().strftime(_TS)


def _ts(value: Any) -> str | None:
    if value is None:
        return None
    if isinstance(value, datetime):
        return value.strftime(_TS)
    return str(value)


def _parse_dt(value: Any) -> datetime | None:
    if value is None:
        return None
    if isinstance(value, datetime):
        dt = value
    else:
        text = str(value).strip().replace("T", " ").replace("Z", "")
        dt = None
        for fmt in (_TS, "%Y-%m-%d %H:%M:%S", "%Y-%m-%d %H:%M:%S%z"):
            try:
                dt = datetime.strptime(text, fmt)
                break
            except ValueError:
                continue
        if dt is None:
            try:
                dt = datetime.fromisoformat(text)
            except ValueError:
                return None
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    return dt.astimezone(timezone.utc)


def _dumps(value: Any) -> str:
    return json.dumps(value, ensure_ascii=False, default=str)


def _loads(text: Any) -> Any:
    if not text:
        return {}
    try:
        return json.loads(text)
    except (TypeError, ValueError):
        return {}


def normalize_email(email: Any) -> str:
    return str(email or "").strip().lower()


def hash_token(token: str) -> str:
    return hashlib.sha256(token.encode("utf-8")).hexdigest()


def password_policy_error(password: Any) -> str | None:
    """The rule the password breaks, in words, or None when it passes."""
    if not isinstance(password, str) or len(password) < MIN_PASSWORD_LENGTH:
        return f"Password must be at least {MIN_PASSWORD_LENGTH} characters long."
    if password.strip() != password:
        return "Password must not start or end with whitespace."
    return None


def _new_id(prefix: str) -> str:
    return f"{prefix}-{secrets.token_hex(8)}"


# --------------------------------------------------------------------------
# Users
# --------------------------------------------------------------------------

_USER_COLUMNS = (
    "id, email, display_name, password_hash, role, status, must_change_password, failed_logins, "
    "locked_until, created_by, created_at, updated_at, last_login_at"
)


def _user_row(r: Any) -> dict[str, Any]:
    return {
        "id": r[0],
        "email": r[1],
        "display_name": r[2] or "",
        "password_hash": r[3],
        "role": r[4],
        "status": r[5],
        "must_change_password": bool(r[6]),
        "failed_logins": int(r[7] or 0),
        "locked_until": _ts(r[8]),
        "created_by": r[9],
        "created_at": _ts(r[10]),
        "updated_at": _ts(r[11]),
        "last_login_at": _ts(r[12]),
    }


def public_user(user: dict[str, Any] | None) -> dict[str, Any] | None:
    """The row without ``password_hash`` and the lockout counters."""
    if not user:
        return None
    return {
        "id": user["id"],
        "email": user["email"],
        "display_name": user.get("display_name") or "",
        "role": user["role"],
        "status": user["status"],
        "must_change_password": bool(user.get("must_change_password")),
        "last_login_at": user.get("last_login_at"),
        "created_at": user.get("created_at"),
    }


def any_users() -> bool:
    init_db()
    row = get_db().execute("SELECT 1 FROM auth_users LIMIT 1").fetchone()
    return row is not None


def count_active_owners() -> int:
    init_db()
    row = get_db().execute(
        "SELECT COUNT(*) FROM auth_users WHERE role = 'owner' AND status = 'active'"
    ).fetchone()
    return int(row[0] or 0) if row else 0


def get_user(user_id: str) -> dict[str, Any] | None:
    init_db()
    row = get_db().execute(f"SELECT {_USER_COLUMNS} FROM auth_users WHERE id = ?", (user_id,)).fetchone()
    return _user_row(row) if row else None


def get_user_by_email(email: str) -> dict[str, Any] | None:
    init_db()
    row = get_db().execute(
        f"SELECT {_USER_COLUMNS} FROM auth_users WHERE email = ?", (normalize_email(email),)
    ).fetchone()
    return _user_row(row) if row else None


def list_users() -> list[dict[str, Any]]:
    init_db()
    rows = get_db().execute(f"SELECT {_USER_COLUMNS} FROM auth_users ORDER BY created_at ASC, id ASC").fetchall()
    return [_user_row(r) for r in rows]


def create_user(
    *,
    email: str,
    role: str,
    display_name: str | None = None,
    password: str | None = None,
    status: str = "active",
    created_by: str | None = None,
    must_change_password: bool = False,
) -> dict[str, Any]:
    """Insert one account. Raises ``ValueError`` for an unknown role/status or a
    policy-breaking password, ``sqlite3.IntegrityError`` for a taken e-mail."""
    if role not in ROLES:
        raise ValueError(f"unknown role: {role}")
    if status not in USER_STATUSES:
        raise ValueError(f"unknown status: {status}")
    email_n = normalize_email(email)
    if not email_n or "@" not in email_n:
        raise ValueError("A valid e-mail address is required.")
    password_hash = None
    if password is not None:
        problem = password_policy_error(password)
        if problem:
            raise ValueError(problem)
        password_hash = generate_password_hash(password)
    init_db()
    db = get_db()
    user_id = _new_id("usr")
    now = _now()
    db.execute(
        "INSERT INTO auth_users (id, email, display_name, password_hash, role, status, must_change_password, "
        "failed_logins, created_by, created_at, updated_at) VALUES (?, ?, ?, ?, ?, ?, ?, 0, ?, ?, ?)",
        (user_id, email_n, (display_name or "").strip() or None, password_hash, role, status,
         1 if must_change_password else 0, created_by, now, now),
    )
    db.commit()
    return get_user(user_id) or {}


def update_user(user_id: str, **fields: Any) -> dict[str, Any] | None:
    """Set ``role`` / ``status`` / ``display_name``; unknown keys are refused."""
    allowed = {"role", "status", "display_name"}
    unknown = set(fields) - allowed
    if unknown:
        raise ValueError(f"cannot update: {', '.join(sorted(unknown))}")
    if "role" in fields and fields["role"] not in ROLES:
        raise ValueError(f"unknown role: {fields['role']}")
    if "status" in fields and fields["status"] not in USER_STATUSES:
        raise ValueError(f"unknown status: {fields['status']}")
    if not fields:
        return get_user(user_id)
    init_db()
    db = get_db()
    sets = ", ".join(f"{k} = ?" for k in fields)
    params = list(fields.values()) + [_now(), user_id]
    db.execute(f"UPDATE auth_users SET {sets}, updated_at = ? WHERE id = ?", tuple(params))
    db.commit()
    return get_user(user_id)


def set_password(user_id: str, password: str, *, must_change: bool = False) -> None:
    problem = password_policy_error(password)
    if problem:
        raise ValueError(problem)
    init_db()
    db = get_db()
    db.execute(
        "UPDATE auth_users SET password_hash = ?, must_change_password = ?, failed_logins = 0, locked_until = NULL, "
        "updated_at = ? WHERE id = ?",
        (generate_password_hash(password), 1 if must_change else 0, _now(), user_id),
    )
    db.commit()


def verify_password(user: dict[str, Any], password: str) -> bool:
    stored = user.get("password_hash")
    if not stored or not isinstance(password, str):
        return False
    return check_password_hash(stored, password)


def locked_until(user: dict[str, Any]) -> datetime | None:
    """The lock's end as a datetime when the account is currently locked."""
    until = _parse_dt(user.get("locked_until"))
    if until and until > _now_dt():
        return until
    return None


def record_login_failure(user_id: str) -> tuple[int, str | None]:
    """Bump the counter; the fifth failure sets ``locked_until``. Returns
    (failed_logins, locked_until text or None)."""
    init_db()
    db = get_db()
    row = db.execute("SELECT failed_logins FROM auth_users WHERE id = ?", (user_id,)).fetchone()
    failures = int(row[0] or 0) + 1 if row else 1
    lock: str | None = None
    if failures >= LOCKOUT_THRESHOLD:
        lock = (_now_dt() + timedelta(minutes=LOCKOUT_MINUTES)).strftime(_TS)
        db.execute(
            "UPDATE auth_users SET failed_logins = 0, locked_until = ?, updated_at = ? WHERE id = ?",
            (lock, _now(), user_id),
        )
    else:
        db.execute(
            "UPDATE auth_users SET failed_logins = ?, updated_at = ? WHERE id = ?", (failures, _now(), user_id)
        )
    db.commit()
    return failures, lock


def record_login_success(user_id: str) -> None:
    init_db()
    db = get_db()
    now = _now()
    db.execute(
        "UPDATE auth_users SET failed_logins = 0, locked_until = NULL, last_login_at = ?, updated_at = ? WHERE id = ?",
        (now, now, user_id),
    )
    db.commit()


# --------------------------------------------------------------------------
# Invitations
# --------------------------------------------------------------------------

_INV_COLUMNS = "id, email, role, token_hash, created_by, created_at, expires_at, accepted_at, accepted_user_id"


def _invitation_row(r: Any) -> dict[str, Any]:
    return {
        "id": r[0],
        "email": r[1],
        "role": r[2],
        "token_hash": r[3],
        "created_by": r[4],
        "created_at": _ts(r[5]),
        "expires_at": _ts(r[6]),
        "accepted_at": _ts(r[7]),
        "accepted_user_id": r[8],
    }


def public_invitation(inv: dict[str, Any]) -> dict[str, Any]:
    expires = _parse_dt(inv.get("expires_at"))
    return {
        "id": inv["id"],
        "email": inv["email"],
        "role": inv["role"],
        "created_by": inv.get("created_by"),
        "created_at": inv.get("created_at"),
        "expires_at": inv.get("expires_at"),
        "accepted_at": inv.get("accepted_at"),
        "expired": bool(expires and expires <= _now_dt() and not inv.get("accepted_at")),
    }


def create_invitation(*, email: str, role: str, created_by: str | None) -> tuple[dict[str, Any], str]:
    """Create the ``invited`` account (or reuse a still-invited one) and a
    token. Returns (invitation row, raw token) — the token is not stored.

    Raises ``ValueError`` when the e-mail belongs to an active or disabled
    account: an invitation must not turn into a password reset for a
    colleague who already has one."""
    if role not in ROLES:
        raise ValueError(f"unknown role: {role}")
    email_n = normalize_email(email)
    if not email_n or "@" not in email_n:
        raise ValueError("A valid e-mail address is required.")
    init_db()
    existing = get_user_by_email(email_n)
    if existing and existing["status"] != "invited":
        raise ValueError("An account with this e-mail already exists.")
    db = get_db()
    if existing:
        db.execute("UPDATE auth_users SET role = ?, updated_at = ? WHERE id = ?", (role, _now(), existing["id"]))
        # The previous link stops working: one open invitation per address.
        db.execute("DELETE FROM auth_invitations WHERE email = ? AND accepted_at IS NULL", (email_n,))
        db.commit()
    else:
        create_user(email=email_n, role=role, status="invited", created_by=created_by)
    token = secrets.token_urlsafe(32)
    inv_id = _new_id("inv")
    now = _now_dt()
    db.execute(
        "INSERT INTO auth_invitations (id, email, role, token_hash, created_by, created_at, expires_at) "
        "VALUES (?, ?, ?, ?, ?, ?, ?)",
        (inv_id, email_n, role, hash_token(token), created_by, now.strftime(_TS),
         (now + timedelta(days=INVITATION_DAYS)).strftime(_TS)),
    )
    db.commit()
    return get_invitation(inv_id) or {}, token


def get_invitation(inv_id: str) -> dict[str, Any] | None:
    init_db()
    row = get_db().execute(f"SELECT {_INV_COLUMNS} FROM auth_invitations WHERE id = ?", (inv_id,)).fetchone()
    return _invitation_row(row) if row else None


def find_invitation_by_token(token: str) -> dict[str, Any] | None:
    """The open, unexpired invitation for this token, else None."""
    if not isinstance(token, str) or not token.strip():
        return None
    init_db()
    row = get_db().execute(
        f"SELECT {_INV_COLUMNS} FROM auth_invitations WHERE token_hash = ? AND accepted_at IS NULL",
        (hash_token(token.strip()),),
    ).fetchone()
    if not row:
        return None
    inv = _invitation_row(row)
    expires = _parse_dt(inv.get("expires_at"))
    if expires and expires <= _now_dt():
        return None
    return inv


def list_invitations(*, include_accepted: bool = False) -> list[dict[str, Any]]:
    init_db()
    sql = f"SELECT {_INV_COLUMNS} FROM auth_invitations"
    if not include_accepted:
        sql += " WHERE accepted_at IS NULL"
    sql += " ORDER BY created_at DESC, id DESC"
    return [_invitation_row(r) for r in get_db().execute(sql).fetchall()]


def delete_invitation(inv_id: str) -> bool:
    """Remove an open invitation; the ``invited`` placeholder account goes with
    it when no other open invitation names the address."""
    init_db()
    inv = get_invitation(inv_id)
    if not inv or inv.get("accepted_at"):
        return False
    db = get_db()
    db.execute("DELETE FROM auth_invitations WHERE id = ?", (inv_id,))
    remaining = db.execute(
        "SELECT 1 FROM auth_invitations WHERE email = ? AND accepted_at IS NULL LIMIT 1", (inv["email"],)
    ).fetchone()
    if remaining is None:
        db.execute("DELETE FROM auth_users WHERE email = ? AND status = 'invited'", (inv["email"],))
    db.commit()
    return True


def accept_invitation(inv: dict[str, Any], *, display_name: str | None, password: str) -> dict[str, Any]:
    """Activate the invited account with its first password. ``ValueError``
    for a policy-breaking password."""
    problem = password_policy_error(password)
    if problem:
        raise ValueError(problem)
    init_db()
    db = get_db()
    user = get_user_by_email(inv["email"])
    now = _now()
    if user is None:
        user = create_user(email=inv["email"], role=inv["role"], display_name=display_name, password=password,
                           created_by=inv.get("created_by"))
    else:
        db.execute(
            "UPDATE auth_users SET password_hash = ?, display_name = COALESCE(?, display_name), role = ?, "
            "status = 'active', must_change_password = 0, failed_logins = 0, locked_until = NULL, updated_at = ? "
            "WHERE id = ?",
            (generate_password_hash(password), (display_name or "").strip() or None, inv["role"], now, user["id"]),
        )
    db.execute(
        "UPDATE auth_invitations SET accepted_at = ?, accepted_user_id = ? WHERE id = ?", (now, user["id"], inv["id"])
    )
    db.commit()
    return get_user(user["id"]) or {}


# --------------------------------------------------------------------------
# Sessions
# --------------------------------------------------------------------------

_SES_COLUMNS = "id, user_id, created_at, last_seen_at, expires_at, revoked_at, ip, user_agent"


def _session_row(r: Any) -> dict[str, Any]:
    return {
        "id": r[0],
        "user_id": r[1],
        "created_at": _ts(r[2]),
        "last_seen_at": _ts(r[3]),
        "expires_at": _ts(r[4]),
        "revoked_at": _ts(r[5]),
        "ip": r[6],
        "user_agent": r[7],
    }


def create_session(user_id: str, *, hours: float, ip: str | None, user_agent: str | None) -> dict[str, Any]:
    init_db()
    db = get_db()
    ses_id = f"ses-{secrets.token_hex(16)}"
    now = _now_dt()
    db.execute(
        "INSERT INTO auth_sessions (id, user_id, created_at, last_seen_at, expires_at, ip, user_agent) "
        "VALUES (?, ?, ?, ?, ?, ?, ?)",
        (ses_id, user_id, now.strftime(_TS), now.strftime(_TS), (now + timedelta(hours=hours)).strftime(_TS),
         (ip or None), (user_agent or "")[:300] or None),
    )
    db.commit()
    return get_session(ses_id) or {}


def get_session(ses_id: str) -> dict[str, Any] | None:
    init_db()
    row = get_db().execute(f"SELECT {_SES_COLUMNS} FROM auth_sessions WHERE id = ?", (ses_id,)).fetchone()
    return _session_row(row) if row else None


def resolve_session(ses_id: str, *, hours: float) -> dict[str, Any] | None:
    """The session's user when the row is live (not revoked, not expired, user
    active); slides ``last_seen_at`` and ``expires_at`` (idle timeout)."""
    if not ses_id:
        return None
    init_db()
    ses = get_session(str(ses_id))
    if not ses or ses.get("revoked_at"):
        return None
    expires = _parse_dt(ses.get("expires_at"))
    now = _now_dt()
    if expires and expires <= now:
        return None
    user = get_user(ses["user_id"])
    if not user or user["status"] != "active":
        return None
    db = get_db()
    db.execute(
        "UPDATE auth_sessions SET last_seen_at = ?, expires_at = ? WHERE id = ?",
        (now.strftime(_TS), (now + timedelta(hours=hours)).strftime(_TS), ses["id"]),
    )
    db.commit()
    return user


def revoke_session(ses_id: str) -> bool:
    init_db()
    db = get_db()
    cur = db.execute(
        "UPDATE auth_sessions SET revoked_at = ? WHERE id = ? AND revoked_at IS NULL", (_now(), ses_id)
    )
    db.commit()
    return bool(cur.rowcount)


def revoke_user_sessions(user_id: str, *, except_session: str | None = None) -> int:
    init_db()
    db = get_db()
    if except_session:
        cur = db.execute(
            "UPDATE auth_sessions SET revoked_at = ? WHERE user_id = ? AND revoked_at IS NULL AND id != ?",
            (_now(), user_id, except_session),
        )
    else:
        cur = db.execute(
            "UPDATE auth_sessions SET revoked_at = ? WHERE user_id = ? AND revoked_at IS NULL", (_now(), user_id)
        )
    db.commit()
    return int(cur.rowcount or 0)


def list_sessions(*, active_only: bool = True, user_id: str | None = None) -> list[dict[str, Any]]:
    init_db()
    sql = f"SELECT {_SES_COLUMNS} FROM auth_sessions"
    where: list[str] = []
    params: list[Any] = []
    if active_only:
        where.append("revoked_at IS NULL AND (expires_at IS NULL OR expires_at > ?)")
        params.append(_now())
    if user_id:
        where.append("user_id = ?")
        params.append(user_id)
    if where:
        sql += " WHERE " + " AND ".join(where)
    sql += " ORDER BY last_seen_at DESC, created_at DESC"
    return [_session_row(r) for r in get_db().execute(sql, tuple(params)).fetchall()]


# --------------------------------------------------------------------------
# Org settings
# --------------------------------------------------------------------------

def get_setting(key: str, default: Any = None) -> Any:
    init_db()
    row = get_db().execute("SELECT value_json FROM org_settings WHERE key = ?", (key,)).fetchone()
    if not row:
        return default
    try:
        return json.loads(row[0]) if row[0] is not None else default
    except (TypeError, ValueError):
        return default


def set_setting(key: str, value: Any) -> None:
    init_db()
    db = get_db()
    db.execute(
        "INSERT INTO org_settings (key, value_json, updated_at) VALUES (?, ?, ?) "
        "ON CONFLICT(key) DO UPDATE SET value_json = excluded.value_json, updated_at = excluded.updated_at",
        (key, _dumps(value), _now()),
    )
    db.commit()


# --------------------------------------------------------------------------
# Account audit
# --------------------------------------------------------------------------

def log_auth_event(
    event_type: str,
    *,
    actor_id: str | None = None,
    actor_role: str | None = None,
    subject_user_id: str | None = None,
    ip: str | None = None,
    user_agent: str | None = None,
    payload: dict[str, Any] | None = None,
) -> int:
    if event_type not in AUTH_EVENT_TYPES:
        raise ValueError(f"unknown auth event type: {event_type}")
    init_db()
    db = get_db()
    cur = db.execute(
        "INSERT INTO auth_audit_events (event_type, actor_id, actor_role, subject_user_id, ip, user_agent, "
        "payload_json, created_at) VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
        (event_type, actor_id, actor_role, subject_user_id, ip, (user_agent or "")[:300] or None,
         _dumps(payload or {}), _now()),
    )
    db.commit()
    return int(cur.lastrowid or 0)


def list_auth_events(
    *,
    actor_id: str | None = None,
    subject_user_id: str | None = None,
    event_type: str | None = None,
    since: str | None = None,
    limit: int = 200,
) -> list[dict[str, Any]]:
    """Newest first. ``actor_id`` matches the actor *or* the subject when
    ``subject_user_id`` is not given separately — a user's activity page wants
    both what they did and what was done to their account."""
    init_db()
    limit = max(1, min(int(limit), 2000))
    sql = ("SELECT id, event_type, actor_id, actor_role, subject_user_id, ip, user_agent, payload_json, created_at "
           "FROM auth_audit_events")
    where: list[str] = []
    params: list[Any] = []
    if actor_id and subject_user_id:
        where.append("actor_id = ? AND subject_user_id = ?")
        params.extend([actor_id, subject_user_id])
    elif actor_id:
        where.append("(actor_id = ? OR subject_user_id = ?)")
        params.extend([actor_id, actor_id])
    elif subject_user_id:
        where.append("subject_user_id = ?")
        params.append(subject_user_id)
    if event_type:
        where.append("event_type = ?")
        params.append(event_type)
    if since:
        where.append("created_at >= ?")
        params.append(since)
    if where:
        sql += " WHERE " + " AND ".join(where)
    sql += " ORDER BY created_at DESC, id DESC LIMIT ?"
    params.append(limit)
    rows = get_db().execute(sql, tuple(params)).fetchall()
    return [
        {
            "id": r[0],
            "event_type": r[1],
            "actor_id": r[2],
            "actor_role": r[3],
            "subject_user_id": r[4],
            "ip": r[5],
            "user_agent": r[6],
            "payload": _loads(r[7]) or {},
            "created_at": _ts(r[8]),
        }
        for r in rows
    ]
