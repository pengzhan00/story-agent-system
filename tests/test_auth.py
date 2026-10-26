"""Team access: password hashing, the permission matrix, sessions and lockout, the API in token mode and its audit."""
from __future__ import annotations

from collections.abc import Iterator
from typing import Any

import pytest
from fastapi.testclient import TestClient

from storyagent.api.app import create_app
from storyagent.auth import (
    AccountLocked,
    AuthError,
    AuthStore,
    InvalidCredentials,
    LastAdmin,
    UserExists,
    WeakPassword,
    decide,
    hash_password,
    verify_password,
)
from storyagent.config import Settings, load_settings
from storyagent.db import Database, migrate

PASSWORD = "correct horse"


def token_settings(fsettings: Settings, mode: str = "token") -> Settings:
    return load_settings({"STORY_AGENT_HOME": str(fsettings.home), "STORY_AGENT_AUTH": mode})


@pytest.fixture
def store(fsettings: Settings) -> AuthStore:
    s = token_settings(fsettings)
    db = Database(s.db_path)
    migrate(db)
    return AuthStore(db, s)


# -- passwords ----------------------------------------------------------------------------------------------
def test_a_password_hash_verifies_only_the_right_password_and_is_salted() -> None:
    a, b = hash_password(PASSWORD), hash_password(PASSWORD)
    assert a != b and a.startswith("scrypt$")
    assert verify_password(PASSWORD, a) and not verify_password("wrong", a)
    assert not verify_password(PASSWORD, "garbage") and not verify_password(PASSWORD, "md5$1$2$3$4$5")


# -- the permission decision --------------------------------------------------------------------------------
def test_the_permission_matrix(fsettings: Settings, store: AuthStore) -> None:
    cfg = store.cfg
    users = {r: store.create_user(f"u_{r}", PASSWORD, r) for r in ("admin", "producer", "editor", "viewer")}

    def verdict(role: str | None, method: str, path: str) -> str | None:
        return decide(cfg, users[role] if role else None, method, path)

    assert verdict(None, "GET", "/api/projects") == "unauthorized"
    assert verdict(None, "POST", "/api/auth/login") is None and verdict(None, "GET", "/api/i18n/en") is None
    assert verdict(None, "GET", "/api/auth/me") is None and verdict(None, "POST", "/api/auth/logout") == "unauthorized"
    assert verdict(None, "GET", "/projects/1") is None                         # the web client's own files
    assert verdict("viewer", "GET", "/api/projects/1/shots") is None
    assert verdict("viewer", "POST", "/api/projects/1/jobs") == "forbidden"
    assert verdict("editor", "POST", "/api/projects/1/jobs") is None
    assert verdict("editor", "PATCH", "/api/projects/1") is None               # editing a project is a plain write
    assert verdict("editor", "POST", "/api/projects") == "forbidden"            # creating one is not
    assert verdict("editor", "DELETE", "/api/projects/1") == "forbidden"
    assert verdict("editor", "POST", "/api/projects/from-text") == "forbidden"
    assert verdict("editor", "POST", "/api/projects/1/restore") == "forbidden"
    assert verdict("producer", "POST", "/api/projects") is None
    assert verdict("producer", "DELETE", "/api/projects/1") is None
    assert verdict("viewer", "POST", "/api/auth/password") is None             # anyone signed in manages their own
    assert verdict(None, "POST", "/api/auth/password") == "unauthorized"
    assert verdict("producer", "GET", "/api/users") == "forbidden"
    assert verdict("editor", "GET", "/api/audit") == "forbidden"
    assert verdict("admin", "GET", "/api/users") is None and verdict("admin", "POST", "/api/users") is None
    for hard in ("/api/maintain/prune-history", "/api/maintain/prune-orphans", "/api/assets/abc/purge",
                 "/api/assets/abc/purge-version"):          # deleting for good (even the audit log) is an admin's call
        assert verdict("producer", "POST", hard) == "forbidden" and verdict("editor", "POST", hard) == "forbidden"
        assert verdict("admin", "POST", hard) is None
    assert verdict("editor", "POST", "/api/assets/abc/trash") is None           # the soft delete stays a plain write
    off = token_settings(fsettings, "off").auth
    assert decide(off, None, "DELETE", "/api/projects/1") is None              # auth off: everything is allowed


# -- accounts, sessions, lockout ----------------------------------------------------------------------------
def test_accounts_are_validated(store: AuthStore) -> None:
    store.create_user("ann", PASSWORD, "editor", "Ann")
    with pytest.raises(UserExists):
        store.create_user("ANN", PASSWORD, "viewer")                          # names are case-insensitive
    with pytest.raises(WeakPassword):
        store.create_user("bob", "short", "viewer")
    with pytest.raises(AuthError, match="unknown role"):
        store.create_user("bob", PASSWORD, "wizard")
    with pytest.raises(AuthError, match="username"):
        store.create_user("b o b", PASSWORD, "viewer")


def test_login_opens_a_session_that_logout_and_expiry_end(store: AuthStore) -> None:
    store.create_user("ann", PASSWORD, "editor")
    token, user, _ = store.login("ann", PASSWORD)
    assert store.authenticate(token) == user
    stored = [str(v) for v in store.db.conn.execute("SELECT * FROM user_sessions").fetchone()]
    assert token not in stored and len(stored[2]) == 64                         # only its SHA-256 is stored
    store.logout(token)
    assert store.authenticate(token) is None
    token2, _, _ = store.login("ann", PASSWORD)
    store.db.conn.execute("UPDATE user_sessions SET expires_at='2000-01-01T00:00:00.000Z'")
    assert store.authenticate(token2) is None


def test_repeated_failures_lock_the_account_even_for_the_right_password(store: AuthStore) -> None:
    store.create_user("ann", PASSWORD, "editor")
    for _ in range(store.cfg.login_max_failures):
        with pytest.raises(InvalidCredentials):
            store.login("ann", "nope nope")
    with pytest.raises(AccountLocked):
        store.login("ann", PASSWORD)
    store.db.conn.execute("UPDATE users SET locked_until='2000-01-01T00:00:00.000Z'")      # time passes
    assert store.login("ann", PASSWORD)[1].username == "ann"
    with pytest.raises(InvalidCredentials):
        store.login("ghost", PASSWORD)                                          # an unknown name looks the same


def test_the_last_admin_stays_and_deactivating_or_resetting_ends_sessions(store: AuthStore) -> None:
    admin = store.create_user("root", PASSWORD, "admin")
    ann = store.create_user("ann", PASSWORD, "editor")
    with pytest.raises(LastAdmin):
        store.update_user(admin.id, role="viewer")
    with pytest.raises(LastAdmin):
        store.update_user(admin.id, active=False)
    second = store.create_user("boss", PASSWORD, "admin")
    assert store.update_user(admin.id, role="viewer").role == "viewer"          # fine once there is another admin
    token, _, _ = store.login("ann", PASSWORD)
    store.update_user(ann.id, password="another password")
    assert store.authenticate(token) is None                                    # a reset signs the user out
    token, _, _ = store.login("ann", "another password")
    store.update_user(ann.id, active=False)
    assert store.authenticate(token) is None
    with pytest.raises(InvalidCredentials):
        store.login("ann", "another password")
    with pytest.raises(LastAdmin):
        store.update_user(second.id, active=False)


# -- the API ------------------------------------------------------------------------------------------------
@pytest.fixture
def api(fsettings: Settings) -> Iterator[tuple[TestClient, AuthStore]]:
    s = token_settings(fsettings)
    with TestClient(create_app(s), raise_server_exceptions=False) as c:
        yield c, c.app.state.ctx.auth                                           # type: ignore[attr-defined]


def login(client: TestClient, name: str, password: str = PASSWORD) -> dict[str, Any]:
    r = client.post("/api/auth/login", json={"username": name, "password": password})
    assert r.status_code == 200, r.text
    return r.json()


def bearer(token: str) -> dict[str, str]:
    return {"Authorization": f"Bearer {token}"}


def test_token_mode_needs_a_sign_in_for_everything_but_the_public_routes(api: tuple[TestClient, AuthStore]) -> None:
    client, store = api
    first = client.get("/api/auth/mode").json()
    assert first["mode"] == "token" and first["first_user"] is True and "en" in first["locales"]
    assert client.get("/api/auth/me").json()["user"] is None                    # public: the web client asks it first
    r = client.get("/api/projects")
    assert r.status_code == 401 and r.json()["error"]["code"] == "unauthorized"
    assert client.get("/api/events").status_code == 401                         # the live feed too
    assert client.get("/api/ready").status_code == 401
    assert client.get("/api/i18n/en").status_code == 200
    assert client.post("/api/auth/login", json={"username": "x", "password": "y"}).status_code == 401
    store.create_user("root", PASSWORD, "admin")
    assert client.get("/api/auth/mode").json()["first_user"] is False


def test_login_by_cookie_and_by_bearer_then_logout(api: tuple[TestClient, AuthStore]) -> None:
    client, store = api
    store.create_user("ann", PASSWORD, "producer", "Ann")
    out = login(client, "ann")
    assert out["user"]["username"] == "ann" and "sa_session" in client.cookies
    me = client.get("/api/auth/me").json()                                     # the cookie from login carries on
    assert me["user"]["username"] == "ann" and me["can_create_project"] and not me["is_admin"]
    assert TestClient(client.app).get("/api/projects", headers=bearer(out["token"])).status_code == 200
    assert client.post("/api/auth/logout").status_code == 204
    assert client.get("/api/projects").status_code == 401
    assert TestClient(client.app).get("/api/projects", headers=bearer(out["token"])).status_code == 401


def test_roles_on_real_routes_and_the_error_envelope(api: tuple[TestClient, AuthStore]) -> None:
    client, store = api
    for name, role in (("root", "admin"), ("pat", "producer"), ("ed", "editor"), ("vi", "viewer")):
        store.create_user(name, PASSWORD, role)
    tokens = {n: login(client, n)["token"] for n in ("root", "pat", "ed", "vi")}
    client.cookies.clear()
    h = {n: bearer(t) for n, t in tokens.items()}
    r = client.post("/api/projects", json={"name": "P"}, headers=h["ed"])
    assert r.status_code == 403 and r.json()["error"]["code"] == "forbidden" and r.json()["error"]["message"]
    assert client.post("/api/projects", json={"name": "P"}, headers=h["vi"]).status_code == 403
    pid = client.post("/api/projects", json={"name": "P"}, headers=h["pat"]).json()["id"]
    assert client.patch(f"/api/projects/{pid}", json={"description": "d"}, headers=h["ed"]).status_code == 200
    assert client.patch(f"/api/projects/{pid}", json={"description": "e"}, headers=h["vi"]).status_code == 403
    assert client.get(f"/api/projects/{pid}", headers=h["vi"]).status_code == 200
    assert client.delete(f"/api/projects/{pid}", headers=h["ed"]).status_code == 403
    assert client.get("/api/users", headers=h["pat"]).status_code == 403
    assert client.get("/api/audit", headers=h["ed"]).status_code == 403
    assert [u["username"] for u in client.get("/api/users", headers=h["root"]).json()] == ["ed", "pat", "root", "vi"]


def test_admin_manages_accounts_and_cannot_remove_the_last_admin(api: tuple[TestClient, AuthStore]) -> None:
    client, store = api
    admin = store.create_user("root", PASSWORD, "admin")
    h = bearer(login(client, "root")["token"])
    client.cookies.clear()
    r = client.post("/api/users", json={"username": "ann", "password": PASSWORD, "role": "editor"}, headers=h)
    assert r.status_code == 201 and "password" not in r.text
    assert client.post("/api/users", json={"username": "ann", "password": PASSWORD, "role": "editor"},
                       headers=h).json()["error"]["code"] == "user_exists"
    assert client.post("/api/users", json={"username": "bo", "password": "x", "role": "editor"},
                       headers=h).json()["error"]["code"] == "weak_password"
    assert client.patch(f"/api/users/{admin.id}", json={"active": False}, headers=h).json()["error"]["code"] \
        == "last_admin"
    assert client.patch(f"/api/users/{r.json()['id']}", json={"role": "viewer"}, headers=h).json()["role"] == "viewer"
    ann_pw = client.post("/api/auth/password", json={"old_password": PASSWORD, "new_password": "brand new pass"},
                         headers=bearer(login(client, "ann")["token"]))
    assert ann_pw.status_code == 204 and login(client, "ann", "brand new pass")


def test_every_write_is_audited_without_bodies_and_denials_are_recorded(api: tuple[TestClient, AuthStore]) -> None:
    client, store = api
    store.create_user("root", PASSWORD, "admin")
    store.create_user("vi", PASSWORD, "viewer")
    h, hv = bearer(login(client, "root")["token"]), bearer(login(client, "vi")["token"])
    client.cookies.clear()
    pid = client.post("/api/projects", json={"name": "Secret plot", "description": "twist"}, headers=h).json()["id"]
    client.patch(f"/api/projects/{pid}", json={"description": "x"}, headers=hv)           # refused
    client.get(f"/api/projects/{pid}", headers=h)                                           # reads are not audited
    rows = client.get("/api/audit?limit=50", headers=h).json()
    http_rows = [r for r in rows if r["action"].startswith("http.")]
    assert [(r["actor"], r["action"], r["status"], r["project_id"]) for r in http_rows] == [
        ("root", "http.POST", 201, None)]
    denied = [r for r in rows if r["action"] == "auth.denied"]
    assert denied and denied[0]["actor"] == "vi" and denied[0]["status"] == 403 and denied[0]["project_id"] == pid
    assert {r["action"] for r in rows} >= {"auth.login", "auth.denied", "http.POST"}
    assert "twist" not in str(rows) and PASSWORD not in str(rows)                          # no bodies, no passwords
    assert client.get("/api/audit?user=vi", headers=h).json()
    assert all(r["actor"] == "vi" for r in client.get("/api/audit?user=vi", headers=h).json())


def test_failed_logins_are_audited(api: tuple[TestClient, AuthStore]) -> None:
    client, store = api
    store.create_user("ann", PASSWORD, "editor")
    assert client.post("/api/auth/login", json={"username": "ann", "password": "wrong"}).status_code == 401
    [row] = store.list_audit(action="auth.login_failed")
    assert row["actor"] == "ann" and row["detail"] == {"reason": "login_failed"}


def test_auth_off_keeps_the_single_operator_flow_and_still_audits_writes(fsettings: Settings) -> None:
    with TestClient(create_app(token_settings(fsettings, "off")), raise_server_exceptions=False) as client:
        assert client.get("/api/auth/mode").json()["mode"] == "off"
        me = client.get("/api/auth/me").json()
        assert me["mode"] == "off" and me["user"] is None and me["is_admin"] and me["can_write"]
        pid = client.post("/api/projects", json={"name": "Solo"}).json()["id"]
        assert client.post("/api/auth/login", json={"username": "a", "password": "b"}).json()["error"]["code"] \
            == "auth_off"
        assert client.get("/api/users").status_code == 200
        [row] = client.get("/api/audit").json()[:1]
        assert row["actor"] == "local" and row["action"] == "http.POST" and pid
