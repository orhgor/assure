"""Local user management (docs/auth.md, 2026-09-27): first-run owner setup,
sign-in with lockout, sessions, invitations, team routes, the permission
matrix on representative routes, actor-identified audit rows, and mode `off`
leaving every existing route open.

Each test runs its own PostgreSQL schema (DATABASE_PATH → schema, conftest);
the auth mode is pinned per test through the environment because the default
resolution reads ``ASSURE_BOOTSTRAP_TOKEN`` and the users table."""

from __future__ import annotations

import pytest

from tests.test_parsure_routes import _seed

TOKEN = "bootstrap-secret-token"
OWNER_PW = "owner-password-123"
PW = "another-password-456"
IP = "203.0.113.7"


@pytest.fixture
def app(tmp_path, monkeypatch):
    monkeypatch.setenv("DATABASE_PATH", str(tmp_path / "auth.sqlite"))
    monkeypatch.setenv("ASSURE_DATA_DIR", str(tmp_path))
    monkeypatch.setenv("WTF_CSRF_ENABLED", "0")
    monkeypatch.setenv("CELERY_BROKER_URL", "")
    monkeypatch.setenv("ASSURE_AUTH_MODE", "local")
    monkeypatch.setenv("ASSURE_BOOTSTRAP_TOKEN", TOKEN)
    monkeypatch.setenv("PROXY_FIX_HOPS", "1")
    monkeypatch.delenv("ASSURE_S3_BUCKET", raising=False)
    import prompt_matrix.history as history_mod
    from prompt_matrix import rbac

    history_mod.DB_PATH = history_mod._resolve_db_path()
    rbac.reset_mode_cache()
    from prompt_matrix.db.connection import init_db
    from prompt_matrix.db.jdf_repository import ensure_project
    from prompt_matrix.web import create_app

    init_db()
    ensure_project("default")
    application = create_app(require_auth=False)
    yield application
    rbac.reset_mode_cache()


def _client(app):
    c = app.test_client()
    c.environ_base["HTTP_X_FORWARDED_FOR"] = IP
    c.environ_base["HTTP_USER_AGENT"] = "pytest/1.0"
    return c


def _setup_owner(app, email="owner@example.com", password=OWNER_PW):
    c = _client(app)
    res = c.post("/api/auth/setup", json={"bootstrap_token": TOKEN, "org_name": "Acme Insurance", "email": email,
                                          "display_name": "Olive Owner", "password": password})
    assert res.status_code == 200, res.get_json()
    return c, res.get_json()["user"]


def _invite_and_accept(owner, app, role, email=None, name=None):
    email = email or f"{role}@example.com"
    res = owner.post("/api/team/invitations", json={"email": email, "role": role})
    assert res.status_code == 200, res.get_json()
    token = res.get_json()["token"]
    c = _client(app)
    acc = c.post("/api/auth/accept-invitation", json={"token": token, "display_name": name or f"{role} person", "password": PW})
    assert acc.status_code == 200, acc.get_json()
    return c, acc.get_json()["user"]


# --------------------------------------------------------------------------
# First run
# --------------------------------------------------------------------------

def test_setup_only_once_and_only_with_the_token(app):
    c = _client(app)
    status = c.get("/api/auth/setup-status").get_json()
    assert status == {"ok": True, "mode": "local", "needs_owner": True, "org_name": "", "bootstrap_required": True}
    # signed-out /api/auth/me: the gate reads user_id
    me = c.get("/api/auth/me").get_json()
    assert me["user_id"] == "" and me["mode"] == "local" and me["needs_owner"] is True
    # everything else is closed
    assert c.get("/api/projects/default/parsure").status_code == 401
    assert c.get("/api/projects/default/parsure").get_json() == {"ok": False, "error": "sign in required"}

    bad = c.post("/api/auth/setup", json={"bootstrap_token": "nope", "email": "o@x.com", "password": OWNER_PW})
    assert bad.status_code == 403 and "ASSURE_BOOTSTRAP_TOKEN" in bad.get_json()["error"]
    short = c.post("/api/auth/setup", json={"bootstrap_token": TOKEN, "email": "o@x.com", "password": "short"})
    assert short.status_code == 400 and "12 characters" in short.get_json()["error"]
    assert c.get("/api/auth/setup-status").get_json()["needs_owner"] is True

    owner, user = _setup_owner(app)
    assert user["role"] == "owner" and user["email"] == "owner@example.com" and "team.manage" in user["permissions"]
    me = owner.get("/api/auth/me").get_json()
    assert me["user_id"] == user["id"] and me["role"] == "owner" and me["mode"] == "local"
    assert me["must_change_password"] is False and me["display_name"] == "Olive Owner" and "audit.read" in me["permissions"]
    status = c.get("/api/auth/setup-status").get_json()
    assert status["needs_owner"] is False and status["org_name"] == "Acme Insurance"

    again = c.post("/api/auth/setup", json={"bootstrap_token": TOKEN, "email": "second@x.com", "password": OWNER_PW})
    assert again.status_code == 403
    users = owner.get("/api/team/users").get_json()["users"]
    assert [u["email"] for u in users] == ["owner@example.com"]
    # the row holds a hash, not the password
    from prompt_matrix.history import get_db
    with app.app_context():
        row = get_db().execute("SELECT password_hash FROM auth_users").fetchone()
    assert row[0].startswith("scrypt:") and OWNER_PW not in row[0]


def test_login_lockout_and_disabled(app):
    owner, _ = _setup_owner(app)
    c = _client(app)
    assert c.post("/api/auth/login", json={"email": "nobody@example.com", "password": OWNER_PW}).status_code == 401
    for i in range(4):
        res = c.post("/api/auth/login", json={"email": "Owner@Example.com", "password": "wrong-password-1"})
        assert res.status_code == 401 and res.get_json()["error"] == "Invalid e-mail or password."
    fifth = c.post("/api/auth/login", json={"email": "owner@example.com", "password": "wrong-password-1"})
    assert fifth.status_code == 423 and fifth.get_json()["locked_until"]
    # the right password is refused while the lock holds
    locked = c.post("/api/auth/login", json={"email": "owner@example.com", "password": OWNER_PW})
    assert locked.status_code == 423 and "locked" in locked.get_json()["error"].lower()
    # the lockout also ended the owner's existing session
    assert owner.get("/api/auth/me").get_json()["user_id"] == ""

    from prompt_matrix.db import auth_repository as repo
    with app.app_context():
        u = repo.get_user_by_email("owner@example.com")
        get_db = __import__("prompt_matrix.history", fromlist=["get_db"]).get_db
        get_db().execute("UPDATE auth_users SET locked_until = NULL WHERE id = ?", (u["id"],))
        get_db().commit()
    ok = c.post("/api/auth/login", json={"email": "owner@example.com", "password": OWNER_PW})
    assert ok.status_code == 200 and ok.get_json()["user"]["role"] == "owner"
    assert c.get("/api/auth/me").get_json()["user_id"] == u["id"]
    events = c.get("/api/audit?event_type=locked").get_json()["events"]
    assert events and events[0]["source"] == "auth" and events[0]["ip"] == IP

    # disabled → 403
    other, other_user = _invite_and_accept(c, app, "reviewer")
    res = c.patch(f"/api/team/users/{other_user['id']}", json={"status": "disabled"})
    assert res.status_code == 200 and res.get_json()["user"]["status"] == "disabled"
    assert other.get("/api/auth/me").get_json()["user_id"] == ""  # session revoked
    res = _client(app).post("/api/auth/login", json={"email": "reviewer@example.com", "password": PW})
    assert res.status_code == 403 and "disabled" in res.get_json()["error"]


def test_sessions_list_and_revoke(app):
    owner, owner_user = _setup_owner(app)
    reviewer, reviewer_user = _invite_and_accept(owner, app, "reviewer")
    sessions = owner.get("/api/team/sessions").get_json()["sessions"]
    assert {s["email"] for s in sessions} == {"owner@example.com", "reviewer@example.com"}
    mine = next(s for s in sessions if s["current"])
    assert mine["user_id"] == owner_user["id"] and mine["ip"] == IP and mine["user_agent"] == "pytest/1.0"
    theirs = next(s for s in sessions if s["user_id"] == reviewer_user["id"])
    assert reviewer.get("/api/auth/me").get_json()["user_id"] == reviewer_user["id"]
    res = owner.delete(f"/api/team/sessions/{theirs['id']}")
    assert res.status_code == 200 and res.get_json()["revoked"] is True
    assert reviewer.get("/api/auth/me").get_json()["user_id"] == ""
    assert reviewer.get("/api/projects/default/parsure").status_code == 401
    assert owner.delete("/api/team/sessions/ses-nope").status_code == 404
    # logout revokes the row
    assert owner.post("/api/auth/logout").get_json() == {"ok": True}
    assert owner.get("/api/auth/me").get_json()["user_id"] == ""
    fresh = _client(app)
    fresh.post("/api/auth/login", json={"email": "owner@example.com", "password": OWNER_PW})
    left = fresh.get("/api/team/sessions").get_json()["sessions"]
    assert len(left) == 1 and left[0]["current"] is True


def test_password_change(app):
    owner, _ = _setup_owner(app)
    assert owner.post("/api/auth/password", json={"current_password": "wrong", "new_password": PW}).status_code == 401
    short = owner.post("/api/auth/password", json={"current_password": OWNER_PW, "new_password": "short"})
    assert short.status_code == 400
    ok = owner.post("/api/auth/password", json={"current_password": OWNER_PW, "new_password": PW})
    assert ok.status_code == 200 and ok.get_json()["must_change_password"] is False
    assert owner.get("/api/auth/me").get_json()["user_id"]  # still signed in
    c = _client(app)
    assert c.post("/api/auth/login", json={"email": "owner@example.com", "password": OWNER_PW}).status_code == 401
    assert c.post("/api/auth/login", json={"email": "owner@example.com", "password": PW}).status_code == 200
    # a signed-out caller cannot change anything
    assert _client(app).post("/api/auth/password", json={"current_password": PW, "new_password": OWNER_PW}).status_code == 401


# --------------------------------------------------------------------------
# Invitations and team
# --------------------------------------------------------------------------

def test_invitations_accept_token_once_hash_stored(app):
    owner, owner_user = _setup_owner(app)
    assert owner.post("/api/team/invitations", json={"email": "x", "role": "reviewer"}).status_code == 400
    assert owner.post("/api/team/invitations", json={"email": "a@b.c", "role": "king"}).status_code == 400
    res = owner.post("/api/team/invitations", json={"email": "Ana@Example.com", "role": "compliance_reviewer"})
    body = res.get_json()
    assert res.status_code == 200 and body["emailed"] is False
    inv, token = body["invitation"], body["token"]
    assert inv["email"] == "ana@example.com" and inv["role"] == "compliance_reviewer" and inv["expires_at"]
    assert body["accept_url"] == f"/accept?token={token}" and len(token) >= 32
    from prompt_matrix.history import get_db
    with app.app_context():
        row = get_db().execute("SELECT token_hash FROM auth_invitations WHERE id = ?", (inv["id"],)).fetchone()
    assert row[0] != token and len(row[0]) == 64 and token not in row[0]
    listed = owner.get("/api/team/invitations").get_json()["invitations"]
    assert [i["id"] for i in listed] == [inv["id"]] and "token" not in listed[0] and "token_hash" not in listed[0]
    users = owner.get("/api/team/users").get_json()["users"]
    assert next(u for u in users if u["email"] == "ana@example.com")["status"] == "invited"
    # a second invitation for an active account is refused
    assert owner.post("/api/team/invitations", json={"email": "owner@example.com", "role": "reviewer"}).status_code == 409

    c = _client(app)
    assert c.post("/api/auth/accept-invitation", json={"token": "bogus", "password": PW}).status_code == 404
    assert c.post("/api/auth/accept-invitation", json={"token": token, "password": "short"}).status_code == 400
    acc = c.post("/api/auth/accept-invitation", json={"token": token, "display_name": "Ana", "password": PW})
    assert acc.status_code == 200
    user = acc.get_json()["user"]
    assert user["role"] == "compliance_reviewer" and "fields.accept_compliance" in user["permissions"]
    assert c.get("/api/auth/me").get_json()["display_name"] == "Ana"
    # the token is spent
    assert _client(app).post("/api/auth/accept-invitation", json={"token": token, "password": PW}).status_code == 404
    assert owner.get("/api/team/invitations").get_json()["invitations"] == []
    events = owner.get("/api/audit?event_type=invitation_accepted").get_json()["events"]
    assert events[0]["actor_id"] == user["id"] and events[0]["payload"]["invitation_id"] == inv["id"]

    # revoke an open invitation: the placeholder account goes with it
    res = owner.post("/api/team/invitations", json={"email": "gone@example.com", "role": "intake"})
    inv_id = res.get_json()["invitation"]["id"]
    assert owner.delete(f"/api/team/invitations/{inv_id}").status_code == 200
    assert owner.delete(f"/api/team/invitations/{inv_id}").status_code == 404
    assert not any(u["email"] == "gone@example.com" for u in owner.get("/api/team/users").get_json()["users"])


def test_team_routes_and_last_owner_protection(app):
    owner, owner_user = _setup_owner(app)
    reviewer, reviewer_user = _invite_and_accept(owner, app, "reviewer")
    # own role
    res = owner.patch(f"/api/team/users/{owner_user['id']}", json={"role": "reviewer"})
    assert res.status_code == 409 and "own role" in res.get_json()["error"]
    # last owner
    assert owner.patch(f"/api/team/users/{owner_user['id']}", json={"status": "disabled"}).status_code == 409
    # unknown user / bad values
    assert owner.patch("/api/team/users/usr-nope", json={"role": "reviewer"}).status_code == 404
    assert owner.patch(f"/api/team/users/{reviewer_user['id']}", json={"role": "god"}).status_code == 400
    assert owner.patch(f"/api/team/users/{reviewer_user['id']}", json={"status": "invited"}).status_code == 400
    # promote, then the first owner may step down
    res = owner.patch(f"/api/team/users/{reviewer_user['id']}", json={"role": "owner", "display_name": "Rae"})
    assert res.status_code == 200 and res.get_json()["user"]["role"] == "owner" and res.get_json()["changed"] == ["display_name", "role"]
    assert reviewer.get("/api/auth/me").get_json()["role"] == "owner"  # takes effect on the live session
    res = reviewer.patch(f"/api/team/users/{owner_user['id']}", json={"role": "auditor"})
    assert res.status_code == 200 and res.get_json()["user"]["role"] == "auditor"
    assert owner.get("/api/team/users").status_code == 403  # no longer manages the team
    # now Rae is the last owner
    assert reviewer.patch(f"/api/team/users/{reviewer_user['id']}", json={"status": "disabled"}).status_code == 409
    # reset password: temporary shown once, must change
    res = reviewer.post(f"/api/team/users/{owner_user['id']}/reset-password")
    assert res.status_code == 200
    temp = res.get_json()["temporary_password"]
    assert res.get_json()["must_change_password"] is True and len(temp) >= 12
    assert owner.get("/api/auth/me").get_json()["user_id"] == ""  # sessions revoked
    c = _client(app)
    login = c.post("/api/auth/login", json={"email": "owner@example.com", "password": temp})
    assert login.status_code == 200 and login.get_json()["user"]["must_change_password"] is True
    assert c.get("/api/auth/me").get_json()["must_change_password"] is True
    assert c.post("/api/auth/password", json={"current_password": temp, "new_password": PW}).status_code == 200
    assert c.get("/api/auth/me").get_json()["must_change_password"] is False
    events = reviewer.get("/api/audit").get_json()["events"]
    kinds = [e["event_type"] for e in events if e["source"] == "auth"]
    assert {"role_changed", "password_reset", "password_changed", "invited", "invitation_accepted", "setup", "login"} <= set(kinds)
    activity = reviewer.get(f"/api/team/users/{owner_user['id']}/activity").get_json()
    assert activity["user"]["id"] == owner_user["id"] and any(e["event_type"] == "password_reset" for e in activity["events"])


# --------------------------------------------------------------------------
# Permission matrix
# --------------------------------------------------------------------------

def _fields(client, rid):
    report = client.get(f"/api/projects/default/parsure/{rid}").get_json()["report"]
    plain = next(f["name"] for f in report["fields"] if f.get("value") is not None and not f.get("compliance_bound"))
    compliance = next(f["name"] for f in report["fields"] if f.get("value") is not None and f.get("compliance_bound"))
    return plain, compliance


def test_permission_matrix_per_role(app):
    owner, _ = _setup_owner(app)
    clients = {"owner": owner}
    for role in ("compliance_reviewer", "reviewer", "intake", "auditor"):
        clients[role], _ = _invite_and_accept(owner, app, role)
    with app.app_context():
        rid = _seed()
    plain, compliance = _fields(owner, rid)

    def denied(res, perm):
        body = res.get_json()
        assert res.status_code == 403, (res.status_code, body)
        assert body["ok"] is False and body["error"] == f"not allowed: {perm}" and body["required"] == [perm] and body["role"]

    # projects.read: every role; the schema registry stays readable
    for role, c in clients.items():
        assert c.get("/api/projects/default/parsure").status_code == 200, role
        assert c.get("/api/parsure/schemas").status_code == 200, role
    assert _client(app).get("/api/parsure/schemas").status_code == 401

    # fields.accept on a plain field
    accept = lambda c, name: c.post(f"/api/projects/default/parsure/{rid}/fields/{name}/accept", json={})
    denied(accept(clients["intake"], plain), "fields.accept")
    denied(accept(clients["auditor"], plain), "fields.accept")
    assert accept(clients["reviewer"], plain).status_code == 200
    # fields.accept_compliance on a compliance-bound field
    res = accept(clients["reviewer"], compliance)
    denied(res, "fields.accept_compliance")
    assert res.get_json()["role"] == "reviewer"
    assert accept(clients["compliance_reviewer"], compliance).status_code == 200

    # fields.correct / fields.dispute / disputes.resolve / classification.override / reports.replay
    correct = lambda c: c.post(f"/api/projects/default/parsure/{rid}/fields/{plain}/correct", json={"value": "1"})
    denied(correct(clients["intake"]), "fields.correct")
    denied(correct(clients["auditor"]), "fields.correct")
    dispute = lambda c: c.post(f"/api/projects/default/parsure/{rid}/fields/{plain}/dispute", json={"reason": "r"})
    denied(dispute(clients["intake"]), "fields.dispute")
    res = dispute(clients["reviewer"])
    assert res.status_code == 201
    dispute_id = res.get_json()["dispute"]["dispute_id"]
    resolve = lambda c: c.post(f"/api/projects/default/parsure/{rid}/disputes/{dispute_id}/resolve", json={"resolution": "ok", "value": 1})
    denied(resolve(clients["reviewer"]), "disputes.resolve")
    denied(resolve(clients["intake"]), "disputes.resolve")
    assert resolve(clients["compliance_reviewer"]).status_code == 200
    classify = lambda c: c.post(f"/api/projects/default/parsure/{rid}/classification", json={"document_type": "auto_claim"})
    denied(classify(clients["intake"]), "classification.override")
    denied(classify(clients["auditor"]), "classification.override")
    replay = lambda c: c.post(f"/api/projects/default/parsure/{rid}/replay", json={})
    denied(replay(clients["reviewer"]), "reports.replay")
    denied(replay(clients["intake"]), "reports.replay")
    assert replay(clients["compliance_reviewer"]).status_code != 403

    # exports.read / exports.dossier
    export = lambda c: c.get(f"/api/projects/default/parsure/{rid}/export?format=json")
    denied(export(clients["intake"]), "exports.read")
    assert export(clients["auditor"]).status_code == 200
    assert export(clients["reviewer"]).status_code == 200
    denied(clients["intake"].get("/api/projects/default/parsure/export?format=csv"), "exports.read")
    dossier = lambda c: c.get("/api/projects/default/export?format=bundle")
    denied(dossier(clients["reviewer"]), "exports.dossier")
    denied(dossier(clients["intake"]), "exports.read")
    assert dossier(clients["auditor"]).status_code not in (401, 403)
    assert clients["reviewer"].get("/api/projects/default/export?format=json").status_code == 200

    # documents.upload / documents.delete / documents.download_original
    upload = lambda c: c.post("/api/projects/default/substrate/upload", data={})
    denied(upload(clients["auditor"]), "documents.upload")
    assert upload(clients["intake"]).status_code == 400  # allowed; no file in the body
    denied(clients["auditor"].post("/api/projects/default/import-pdf", json={}), "documents.upload")
    denied(clients["auditor"].post("/api/projects/default/jdf/ingest", data={}), "documents.upload")
    delete = lambda c: c.delete("/api/projects/default/substrate/none")
    denied(delete(clients["intake"]), "documents.delete")
    denied(delete(clients["reviewer"]), "documents.delete")
    assert delete(clients["owner"]).status_code == 404
    original = lambda c: c.get("/api/projects/default/documents/doc-none/original")
    denied(original(clients["intake"]), "documents.download_original")
    denied(original(clients["reviewer"]), "documents.download_original")
    assert original(clients["auditor"]).status_code == 404

    # compile.run
    draft = lambda c: c.post("/api/projects/default/draft/stream", json={})
    denied(draft(clients["intake"]), "compile.run")
    denied(draft(clients["auditor"]), "compile.run")
    assert draft(clients["reviewer"]).status_code not in (401, 403)
    denied(clients["intake"].post("/api/projects/default/inquire/stream", json={}), "compile.run")

    # settings.integrations
    save = lambda c: c.put("/api/integrations/aws", json={})
    denied(save(clients["compliance_reviewer"]), "settings.integrations")
    denied(save(clients["reviewer"]), "settings.integrations")
    assert save(clients["owner"]).status_code == 400  # allowed; bucket is required
    denied(clients["auditor"].delete("/api/integrations/aws"), "settings.integrations")

    # team.manage / audit.read
    for role in ("compliance_reviewer", "reviewer", "intake", "auditor"):
        denied(clients[role].get("/api/team/users"), "team.manage")
        denied(clients[role].post("/api/team/invitations", json={"email": "z@z.z", "role": "intake"}), "team.manage")
    for role in ("owner", "compliance_reviewer", "auditor"):
        assert clients[role].get("/api/audit").status_code == 200, role
    for role in ("reviewer", "intake"):
        denied(clients[role].get("/api/audit"), "audit.read")


def test_permissions_module_matrix():
    from prompt_matrix import rbac

    assert rbac.permissions_for("owner") == frozenset(rbac.PERMISSIONS)
    assert rbac.permissions_for("compliance_reviewer") == frozenset(rbac.PERMISSIONS) - {
        "team.manage", "settings.integrations", "settings.schemas", "settings.models"}
    assert rbac.permissions_for("reviewer") == {
        "projects.read", "projects.create", "documents.upload", "fields.accept", "fields.correct", "fields.dispute",
        "classification.override", "exports.read", "compile.run", "sources.manage"}
    assert rbac.permissions_for("intake") == {"projects.read", "documents.upload", "sources.manage"}
    assert rbac.permissions_for("auditor") == {
        "projects.read", "exports.read", "exports.dossier", "documents.download_original", "audit.read"}
    assert rbac.permissions_for("nobody") == frozenset()
    with pytest.raises(ValueError):
        rbac.requires("not.a.permission")


# --------------------------------------------------------------------------
# Audit rows
# --------------------------------------------------------------------------

def test_audit_rows_carry_actor_id_role_and_ip(app):
    owner, owner_user = _setup_owner(app)
    reviewer, reviewer_user = _invite_and_accept(owner, app, "reviewer", name="Rae Reviewer")
    with app.app_context():
        rid = _seed()
    plain, _ = _fields(owner, rid)
    # no actor in the body → the account's display name; the id/role/ip always
    assert reviewer.post(f"/api/projects/default/parsure/{rid}/fields/{plain}/accept", json={}).status_code == 200
    events = owner.get(f"/api/projects/default/parsure/audit-log?report_id={rid}").get_json()["events"]
    e = events[0]
    assert e["event_type"] == "field_accepted" and e["actor"] == "Rae Reviewer"
    assert e["actor_id"] == reviewer_user["id"] and e["actor_role"] == "reviewer" and e["ip"] == IP
    # a body actor is kept as text; the account is still recorded
    reviewer.post(f"/api/projects/default/parsure/{rid}/fields/{plain}/correct", json={"value": "2", "actor": "typed name"})
    e = owner.get(f"/api/projects/default/parsure/audit-log?report_id={rid}").get_json()["events"][0]
    assert e["event_type"] == "field_corrected" and e["actor"] == "typed name" and e["actor_id"] == reviewer_user["id"]
    # the merged feed: both sources, newest first, filterable by actor
    feed = owner.get("/api/audit").get_json()
    assert feed["ok"] and {ev["source"] for ev in feed["events"]} == {"parsure", "auth"}
    stamps = [ev["created_at"] for ev in feed["events"]]
    assert stamps == sorted(stamps, reverse=True)
    mine = owner.get(f"/api/audit?actor_id={reviewer_user['id']}").get_json()["events"]
    assert mine and all(ev["actor_id"] == reviewer_user["id"] or ev.get("subject_user_id") == reviewer_user["id"] for ev in mine)
    assert {ev["event_type"] for ev in mine} >= {"field_accepted", "field_corrected", "invitation_accepted"}
    scoped = owner.get("/api/audit?project_id=default").get_json()["events"]
    assert scoped and all(ev["source"] == "parsure" for ev in scoped)
    for ev in scoped:
        assert set(ev) >= {"id", "source", "event_type", "project_id", "report_id", "field_name", "actor", "actor_id",
                           "actor_role", "ip", "payload", "created_at"}
    # pipeline-written events (no request) carry no actor
    with app.app_context():
        from prompt_matrix.db import parsure_repository as repo
        pipeline = [ev for ev in repo.list_events("default", report_id=rid) if ev["event_type"] == "intake_received"]
    assert pipeline and pipeline[0]["actor_id"] is None and pipeline[0]["ip"] is None


def test_pages_redirect_to_signin_when_signed_out(app):
    c = _client(app)
    res = c.get("/parsing?project_id=default")
    assert res.status_code == 302 and res.headers["Location"].startswith("/signin?next=%2Fparsing")
    owner, _ = _setup_owner(app)
    assert owner.get("/parsing?project_id=default").status_code == 200


# --------------------------------------------------------------------------
# Mode off
# --------------------------------------------------------------------------

def test_mode_off_leaves_existing_routes_open(tmp_path, monkeypatch):
    monkeypatch.setenv("DATABASE_PATH", str(tmp_path / "off.sqlite"))
    monkeypatch.setenv("ASSURE_DATA_DIR", str(tmp_path))
    monkeypatch.setenv("WTF_CSRF_ENABLED", "0")
    monkeypatch.setenv("CELERY_BROKER_URL", "")
    # Empty, not deleted: create_app loads the developer's .env (load_dotenv
    # fills only missing keys), and a laptop with local sign-in switched on
    # would otherwise hand the test its bootstrap token (2026-09-27).
    monkeypatch.setenv("ASSURE_AUTH_MODE", "")
    monkeypatch.setenv("ASSURE_BOOTSTRAP_TOKEN", "")
    monkeypatch.delenv("CLERK_SECRET_KEY", raising=False)
    monkeypatch.delenv("CLERK_PUBLISHABLE_KEY", raising=False)
    monkeypatch.delenv("ASSURE_S3_BUCKET", raising=False)
    import prompt_matrix.history as history_mod
    from prompt_matrix import rbac

    history_mod.DB_PATH = history_mod._resolve_db_path()
    rbac.reset_mode_cache()
    from prompt_matrix.db.connection import init_db
    from prompt_matrix.db.jdf_repository import ensure_project
    from prompt_matrix.web import create_app

    init_db()
    ensure_project("default")
    c = create_app(require_auth=False).test_client()
    assert rbac.auth_mode() == "off"
    assert c.get("/api/auth/setup-status").get_json()["mode"] == "off"
    assert c.get("/api/auth/me").get_json() == {"ok": True, "user_id": "", "mode": "off", "needs_owner": False}
    assert c.get("/api/auth/config").get_json()["mode"] == "off"
    assert c.get("/api/projects/default/parsure").status_code == 200
    assert c.get("/api/parsure/schemas").status_code == 200
    assert c.get("/api/team/users").status_code == 200  # decorator is a no-op: owner-equivalent
    assert c.post("/api/auth/login", json={"email": "a@b.c", "password": PW}).status_code == 403
    assert c.post("/api/auth/setup", json={"bootstrap_token": "x", "email": "a@b.c", "password": PW}).status_code == 403
    assert c.get("/parsing?project_id=default").status_code == 200
    # a bootstrap token alone flips the default to local
    monkeypatch.setenv("ASSURE_BOOTSTRAP_TOKEN", TOKEN)
    rbac.reset_mode_cache()
    assert rbac.auth_mode() == "local"
    assert c.get("/api/projects/default/parsure").status_code == 401
    rbac.reset_mode_cache()
