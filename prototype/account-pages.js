/* Team and Audit pages (local user management, 2026-09-27).
   Standalone pages under the same gate as the shell; this file is their only
   script. Strings come from /api/i18n (the shell's catalog, `shell.team.*` and
   `shell.audit.*`); a 401 sends the reader to /signin, a 403 shows the
   server's sentence where the action was. Nothing here caches a secret: the
   invitation token and a temporary password are shown once, from the answer,
   and never stored. */
(function () {
  "use strict";
  var STRINGS = {};
  function T(key, fallback) { var v = STRINGS[key]; return (typeof v === "string" && v) ? v : fallback; }
  function TF(key, fallback, vars) {
    var s = T(key, fallback);
    Object.keys(vars || {}).forEach(function (k) { s = s.split("{" + k + "}").join(String(vars[k])); });
    return s;
  }
  function el(tag, cls, text) { var e = document.createElement(tag); if (cls) e.className = cls; if (text != null) e.textContent = String(text); return e; }
  function api(method, url, body) {
    var init = { method: method, credentials: "same-origin", headers: { Accept: "application/json" } };
    if (body !== undefined) { init.headers["Content-Type"] = "application/json"; init.body = JSON.stringify(body); }
    return fetch(url, init).then(function (r) {
      return r.json().catch(function () { return {}; }).then(function (j) {
        if (r.status === 401) {
          var here = window.location.pathname + window.location.search;
          window.location.replace("/signin?next=" + encodeURIComponent(here));
          throw new Error(T("shell.auth.sign_in_required", "Sign in required"));
        }
        if (!r.ok || j.ok === false) { var e = new Error(j.error || ("HTTP " + r.status)); e.status = r.status; e.body = j; throw e; }
        return j;
      });
    });
  }
  function say(target, text, tone) {
    var n = typeof target === "string" ? document.getElementById(target) : target;
    if (!n) return;
    n.textContent = text || "";
    if (tone) n.setAttribute("data-tone", tone); else n.removeAttribute("data-tone");
  }
  function whenWords(s) {
    if (!s) return "—";
    var t = String(s).trim();
    if (/^\d{4}-\d{2}-\d{2} \d{2}:\d{2}:\d{2}$/.test(t)) t = t.replace(" ", "T") + "Z";
    var ms = Date.parse(t);
    if (isNaN(ms)) return String(s);
    try { return new Date(ms).toLocaleString(undefined, { dateStyle: "medium", timeStyle: "short" }); } catch (_) { return new Date(ms).toISOString(); }
  }
  function roleWords(role) { var r = String(role || ""); return r ? T("shell.auth.role." + r, r.replace(/_/g, " ")) : T("shell.auth.role.none", "no role"); }
  var ROLES = ["owner", "compliance_reviewer", "reviewer", "intake", "auditor"];
  function copyButton(value) {
    var b = el("button", "btn-tertiary copy-btn", T("shell.team.copy", "Copy"));
    b.type = "button";
    b.addEventListener("click", function () {
      var done = function () { b.textContent = T("shell.team.copied", "Copied"); setTimeout(function () { b.textContent = T("shell.team.copy", "Copy"); }, 1800); };
      if (navigator.clipboard && navigator.clipboard.writeText) navigator.clipboard.writeText(value).then(done, done);
      else { try { var ta = document.createElement("textarea"); ta.value = value; document.body.appendChild(ta); ta.select(); document.execCommand("copy"); ta.remove(); } catch (_) {} done(); }
    });
    return b;
  }
  function applyI18n() {
    document.querySelectorAll("[data-i18n]").forEach(function (n) { n.textContent = T(n.getAttribute("data-i18n"), n.textContent); });
    document.querySelectorAll("[data-i18n-placeholder]").forEach(function (n) { n.placeholder = T(n.getAttribute("data-i18n-placeholder"), n.placeholder); });
  }
  function identity(me) {
    var id = document.getElementById("page-identity");
    if (!id) return;
    if (!me || !me.user_id) { id.hidden = true; return; }
    id.hidden = false;
    var n = document.getElementById("identity-name"); if (n) n.textContent = me.display_name || me.email || me.user_id;
    var r = document.getElementById("identity-role"); if (r) r.textContent = roleWords(me.role);
    var so = document.getElementById("page-signout");
    if (so) so.addEventListener("click", function () { api("POST", "/api/auth/logout", {}).catch(function () {}).then(function () { window.location.replace("/signin"); }); });
  }
  function can(me, perm) { return !me || !Array.isArray(me.permissions) || me.permissions.indexOf(perm) !== -1; }
  function denied(perm, me) {
    var main = document.getElementById("page-main");
    if (!main) return;
    main.innerHTML = "";
    main.appendChild(el("p", "page-denied", TF("shell.auth.not_allowed", "Not allowed for your role ({role}): {perm}. An owner can change it.", { role: roleWords(me && me.role), perm: perm })));
  }

  // ---------------------------------------------------------------- Team
  function teamPage(me) {
    if (!can(me, "team.manage")) { denied("team.manage", me); return; }
    var usersBody = document.getElementById("users-body");
    var invBody = document.getElementById("invitations-body");
    var sessBody = document.getElementById("sessions-body");
    function loadUsers() {
      return api("GET", "/api/team/users").then(function (j) {
        var users = Array.isArray(j.users) ? j.users : [];
        usersBody.innerHTML = "";
        if (!users.length) { usersBody.appendChild(rowNote(T("shell.team.no_users", "No accounts yet."), 6)); }
        users.forEach(function (u) {
          var tr = el("tr", "user-row"); tr.setAttribute("data-user-id", u.id); tr.setAttribute("data-status", u.status || "");
          var who = el("td", "c-who"); who.appendChild(el("b", null, u.display_name || u.email)); who.appendChild(el("small", null, u.email)); tr.appendChild(who);
          var roleTd = el("td", "c-role");
          var sel = document.createElement("select"); sel.className = "role-select"; sel.setAttribute("aria-label", T("shell.team.role", "Role"));
          ROLES.forEach(function (r) { var o = document.createElement("option"); o.value = r; o.textContent = roleWords(r); if (r === u.role) o.selected = true; sel.appendChild(o); });
          if (u.id === me.user_id) sel.disabled = true;   // one's own role is changed by another owner (409 on the server too)
          var note = el("span", "row-note");
          sel.addEventListener("change", function () {
            api("PATCH", "/api/team/users/" + encodeURIComponent(u.id), { role: sel.value })
              .then(function () { say(note, T("shell.team.saved", "Saved")); setTimeout(function () { say(note, ""); }, 1500); })
              .catch(function (e) { sel.value = u.role; say(note, e.message, "error"); });
          });
          roleTd.appendChild(sel); roleTd.appendChild(note); tr.appendChild(roleTd);
          var st = el("td", "c-status");
          var active = String(u.status || "active") === "active";
          var toggle = el("button", "btn-tertiary status-toggle", active ? T("shell.team.disable", "Disable") : T("shell.team.enable", "Enable")); toggle.type = "button";
          if (u.id === me.user_id) toggle.disabled = true;
          var stNote = el("span", "row-note");
          toggle.addEventListener("click", function () {
            api("PATCH", "/api/team/users/" + encodeURIComponent(u.id), { status: active ? "disabled" : "active" })
              .then(loadUsers).catch(function (e) { say(stNote, e.message, "error"); });
          });
          st.appendChild(el("span", "status-word status--" + (active ? "active" : "disabled"), active ? T("shell.team.active", "active") : T("shell.team.disabled", "disabled")));
          if (u.must_change_password) st.appendChild(el("small", null, T("shell.team.must_change", "temporary password")));
          st.appendChild(toggle); st.appendChild(stNote); tr.appendChild(st);
          tr.appendChild(el("td", "c-when", whenWords(u.last_login_at)));
          tr.appendChild(el("td", "c-when", whenWords(u.created_at)));
          var act = el("td", "c-act");
          var reset = el("button", "btn-tertiary reset-btn", T("shell.team.reset_password", "Reset password")); reset.type = "button";
          var out = el("div", "once"); out.hidden = true;
          reset.addEventListener("click", function () {
            if (!window.confirm(TF("shell.team.reset_confirm", "Reset the password of {who}? The current one stops working at once.", { who: u.email }))) return;
            api("POST", "/api/team/users/" + encodeURIComponent(u.id) + "/reset-password", {})
              .then(function (j) {
                out.innerHTML = ""; out.hidden = false;
                out.appendChild(el("span", "once-label", T("shell.team.temporary_password", "Temporary password — shown once")));
                var code = el("code", "once-value", j.temporary_password || ""); out.appendChild(code);
                out.appendChild(copyButton(j.temporary_password || ""));
              })
              .catch(function (e) { out.hidden = false; out.textContent = e.message; out.setAttribute("data-tone", "error"); });
          });
          act.appendChild(reset); act.appendChild(out); tr.appendChild(act);
          usersBody.appendChild(tr);
        });
      }).catch(function (e) { usersBody.innerHTML = ""; usersBody.appendChild(rowNote(e.message, 6, "error")); });
    }
    function rowNote(text, span, tone) { var tr = el("tr", "note-row"); var td = el("td", null, text); td.colSpan = span; if (tone) td.setAttribute("data-tone", tone); tr.appendChild(td); return tr; }
    function loadInvitations() {
      return api("GET", "/api/team/invitations").then(function (j) {
        var invs = Array.isArray(j.invitations) ? j.invitations : [];
        invBody.innerHTML = "";
        if (!invs.length) { invBody.appendChild(rowNote(T("shell.team.no_invitations", "No pending invitations."), 4)); return; }
        invs.forEach(function (inv) {
          var tr = el("tr", "invitation-row"); tr.setAttribute("data-invitation-id", inv.id);
          tr.appendChild(el("td", null, inv.email)); tr.appendChild(el("td", null, roleWords(inv.role))); tr.appendChild(el("td", "c-when", whenWords(inv.expires_at)));
          var act = el("td", "c-act");
          var revoke = el("button", "btn-tertiary revoke-btn", T("shell.team.revoke", "Revoke")); revoke.type = "button";
          revoke.addEventListener("click", function () {
            api("DELETE", "/api/team/invitations/" + encodeURIComponent(inv.id)).then(loadInvitations).catch(function (e) { say("invite-note", e.message, "error"); });
          });
          act.appendChild(revoke); tr.appendChild(act); invBody.appendChild(tr);
        });
      }).catch(function (e) { invBody.innerHTML = ""; invBody.appendChild(rowNote(e.message, 4, "error")); });
    }
    function loadSessions() {
      return api("GET", "/api/team/sessions").then(function (j) {
        var sessions = Array.isArray(j.sessions) ? j.sessions : [];
        sessBody.innerHTML = "";
        if (!sessions.length) { sessBody.appendChild(rowNote(T("shell.team.no_sessions", "No active sessions."), 5)); return; }
        sessions.forEach(function (sx) {
          var tr = el("tr", "session-row"); tr.setAttribute("data-session-id", sx.id);
          tr.appendChild(el("td", null, sx.display_name || sx.email || sx.user_id || "—"));
          tr.appendChild(el("td", null, sx.ip || "—"));
          tr.appendChild(el("td", "c-when", whenWords(sx.created_at || sx.started_at)));
          tr.appendChild(el("td", "c-when", whenWords(sx.last_seen_at || sx.updated_at)));
          var act = el("td", "c-act");
          var revoke = el("button", "btn-tertiary revoke-btn", T("shell.team.revoke", "Revoke")); revoke.type = "button";
          if (sx.current) { revoke.disabled = true; revoke.title = T("shell.team.this_session", "This session"); }
          revoke.addEventListener("click", function () {
            api("DELETE", "/api/team/sessions/" + encodeURIComponent(sx.id)).then(loadSessions).catch(function (e) { say("sessions-note", e.message, "error"); });
          });
          act.appendChild(revoke); tr.appendChild(act); sessBody.appendChild(tr);
        });
      }).catch(function (e) { sessBody.innerHTML = ""; sessBody.appendChild(rowNote(e.message, 5, "error")); });
    }
    var inviteForm = document.getElementById("invite-form");
    var roleSel = document.getElementById("invite-role");
    ROLES.forEach(function (r) { var o = document.createElement("option"); o.value = r; o.textContent = roleWords(r); if (r === "reviewer") o.selected = true; roleSel.appendChild(o); });
    inviteForm.addEventListener("submit", function (e) {
      e.preventDefault();
      var email = String(document.getElementById("invite-email").value || "").trim();
      if (!email) return;
      var btn = document.getElementById("invite-submit"); btn.disabled = true;
      say("invite-note", "");
      api("POST", "/api/team/invitations", { email: email, role: roleSel.value })
        .then(function (j) {
          var out = document.getElementById("invite-result"); out.innerHTML = ""; out.hidden = false;
          var inv = j.invitation || {};
          out.appendChild(el("p", "once-label", TF("shell.team.invited", "Invitation for {email} as {role} — the link is shown once.", { email: inv.email || email, role: roleWords(inv.role || roleSel.value) })));
          if (j.accept_url) { var link = el("div", "once-row"); link.appendChild(el("code", "once-value", j.accept_url)); link.appendChild(copyButton(j.accept_url)); out.appendChild(link); }
          if (j.token) { var tok = el("div", "once-row"); tok.appendChild(el("span", "once-key", T("shell.team.token", "Token"))); tok.appendChild(el("code", "once-value", j.token)); tok.appendChild(copyButton(j.token)); out.appendChild(tok); }
          out.appendChild(el("p", "once-mail", j.emailed ? T("shell.team.emailed", "E-mailed to the address.") : T("shell.team.not_emailed", "Not e-mailed — pass the link on yourself.")));
          if (inv.expires_at) out.appendChild(el("p", "once-mail", TF("shell.team.expires", "Expires {when}", { when: whenWords(inv.expires_at) })));
          document.getElementById("invite-email").value = "";
          loadInvitations();
        })
        .catch(function (err) { say("invite-note", err.message, "error"); })
        .then(function () { btn.disabled = false; });
    });
    loadUsers(); loadInvitations(); loadSessions();
  }

  // --------------------------------------------------------------- Audit
  function auditPage(me) {
    if (!can(me, "audit.read")) { denied("audit.read", me); return; }
    var qs = new URLSearchParams(window.location.search || "");
    var projectSel = document.getElementById("f-project");
    var actor = document.getElementById("f-actor");
    var type = document.getElementById("f-type");
    var since = document.getElementById("f-since");
    var body = document.getElementById("audit-body");
    var meta = document.getElementById("audit-meta");
    var preselect = qs.get("project_id") || "";
    api("GET", "/api/projects").then(function (j) {
      var projects = Array.isArray(j.projects) ? j.projects : (Array.isArray(j) ? j : []);
      projects.forEach(function (p) { var o = document.createElement("option"); o.value = p.id; o.textContent = p.title || p.id; if (p.id === preselect) o.selected = true; projectSel.appendChild(o); });
      if (preselect && !Array.prototype.some.call(projectSel.options, function (o) { return o.value === preselect; })) { var o = document.createElement("option"); o.value = preselect; o.textContent = preselect; o.selected = true; projectSel.appendChild(o); }
    }).catch(function () {}).then(load);
    function payloadSummary(p) {
      if (!p || typeof p !== "object") return "";
      var bits = [];
      Object.keys(p).slice(0, 4).forEach(function (k) {
        var v = p[k];
        if (v == null) return;
        if (typeof v === "object") v = Array.isArray(v) ? v.length + " items" : Object.keys(v).slice(0, 3).map(function (kk) { return kk + "=" + String(v[kk]); }).join(", ");
        bits.push(k + ": " + String(v).slice(0, 60));
      });
      return bits.join(" · ");
    }
    function load() {
      var params = new URLSearchParams();
      if (projectSel.value) params.set("project_id", projectSel.value);
      if (actor.value.trim()) params.set("actor_id", actor.value.trim());
      if (type.value.trim()) params.set("event_type", type.value.trim());
      if (since.value) params.set("since", since.value);
      params.set("limit", "200");
      body.innerHTML = ""; say(meta, T("shell.audit.loading", "Loading…"));
      api("GET", "/api/audit?" + params.toString()).then(function (j) {
        var events = Array.isArray(j.events) ? j.events : [];
        say(meta, events.length ? TF("shell.audit.count", "{n} events", { n: events.length }) : T("shell.audit.none", "No events match."));
        events.forEach(function (ev) {
          var tr = el("tr", "audit-row"); tr.setAttribute("data-source", ev.source || ""); tr.setAttribute("data-event-type", ev.event_type || "");
          tr.appendChild(el("td", "c-when", whenWords(ev.created_at)));
          var who = el("td", "c-who"); who.appendChild(el("b", null, ev.actor || ev.actor_id || "—")); if (ev.actor_role) who.appendChild(el("small", null, roleWords(ev.actor_role))); tr.appendChild(who);
          tr.appendChild(el("td", "c-ip", ev.ip || "—"));
          var evt = el("td", "c-event"); evt.appendChild(el("span", "event-word", String(ev.event_type || "").replace(/_/g, " "))); if (ev.source) evt.appendChild(el("small", null, ev.source)); tr.appendChild(evt);
          var where = el("td", "c-where");
          if (ev.report_id) { var a = el("a", null, ev.report_id); a.href = "/parsing/" + encodeURIComponent(ev.report_id) + (ev.project_id ? "?project_id=" + encodeURIComponent(ev.project_id) : ""); where.appendChild(a); }
          else if (ev.project_id) where.appendChild(el("span", null, ev.project_id));
          else where.textContent = "—";
          if (ev.field_name) where.appendChild(el("small", null, ev.field_name));
          tr.appendChild(where);
          tr.appendChild(el("td", "c-payload", payloadSummary(ev.payload)));
          body.appendChild(tr);
        });
      }).catch(function (e) { say(meta, e.message, "error"); });
    }
    document.getElementById("audit-filters").addEventListener("submit", function (e) { e.preventDefault(); load(); });
    projectSel.addEventListener("change", load);
  }

  document.addEventListener("DOMContentLoaded", function () {
    fetch("/api/i18n", { credentials: "same-origin", headers: { Accept: "application/json" } })
      .then(function (r) { return r.ok ? r.json() : {}; }).catch(function () { return {}; })
      .then(function (p) { STRINGS = (p && p.strings) || {}; applyI18n(); })
      .then(function () { return api("GET", "/api/auth/me").catch(function () { return {}; }); })
      .then(function (me) {
        identity(me);
        var page = document.body.getAttribute("data-page");
        if (page === "team") teamPage(me);
        else if (page === "audit") auditPage(me);
      });
  });
})();
