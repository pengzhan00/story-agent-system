"""Accounts with an admin-approved age: registration, the review, the age that grows by itself, the age-rated projects
(PG-13, 15+, restricted) and what a younger or unapproved account is refused."""
from __future__ import annotations

from collections.abc import Iterator
from datetime import UTC, date, datetime
from typing import Any

import pytest
from fastapi.testclient import TestClient

from storyagent.api.app import create_app
from storyagent.auth import AuthStore, BadBirthDate, RegistrationClosed, age_on, parse_birth_date
from storyagent.config import ConfigError, Settings, load_settings
from storyagent.config.content import build_content
from storyagent.db import Database, migrate

PASSWORD = "correct horse"


def years_ago(n: int, extra_days: int = 0) -> str:
    today = datetime.now(UTC).date()
    try:
        born = today.replace(year=today.year - n)
    except ValueError:                       # 29 February
        born = today.replace(year=today.year - n, day=28)
    return date.fromordinal(born.toordinal() - extra_days).isoformat()


@pytest.fixture
def asettings(fsettings: Settings) -> Settings:
    return load_settings({"STORY_AGENT_HOME": str(fsettings.home), "STORY_AGENT_AUTH": "token"})


@pytest.fixture
def store(asettings: Settings) -> AuthStore:
    db = Database(asettings.db_path)
    migrate(db)
    return AuthStore(db, asettings)


@pytest.fixture
def api(asettings: Settings) -> Iterator[tuple[TestClient, AuthStore]]:
    with TestClient(create_app(asettings), raise_server_exceptions=False) as c:
        yield c, c.app.state.ctx.auth                                           # type: ignore[attr-defined]


def bearer(token: str) -> dict[str, str]:
    return {"Authorization": f"Bearer {token}"}


def login(client: TestClient, name: str) -> dict[str, str]:
    r = client.post("/api/auth/login", json={"username": name, "password": PASSWORD})
    assert r.status_code == 200, r.text
    client.cookies.clear()
    return bearer(r.json()["token"])


# -- the age itself ------------------------------------------------------------------------------------------
def test_age_is_worked_out_from_the_birth_date_and_grows() -> None:
    assert age_on("2000-06-15", date(2026, 6, 14)) == 25 and age_on("2000-06-15", date(2026, 6, 15)) == 26
    assert age_on("2000-02-29", date(2026, 2, 28)) == 25 and age_on("2000-02-29", date(2026, 3, 1)) == 26
    assert age_on(None) is None and age_on("not a date") is None


def test_a_birth_date_must_be_a_real_past_date() -> None:
    assert parse_birth_date("2001-05-17") == date(2001, 5, 17)
    for bad in ("", "17/05/2001", "2999-01-01", "1800-01-01", "2001-13-01"):
        with pytest.raises(BadBirthDate):
            parse_birth_date(bad)


# -- the ratings ---------------------------------------------------------------------------------------------
def test_the_default_ratings_climb_with_age(asettings: Settings) -> None:
    ratings = sorted(asettings.content.ratings.values(), key=lambda p: p.rank)
    assert [(r.name, r.min_age) for r in ratings] == [("general", 0), ("pg13", 13), ("age15", 15), ("restricted", 18)]


def test_an_age_limit_may_not_fall_as_the_rank_rises_and_gates_need_adults() -> None:
    def build(extra: str) -> None:
        import tomllib
        raw = tomllib.loads('[content]\ndefault_rating="general"\n[content.ratings.general]\nrank=0\n' + extra)
        build_content(raw, ConfigError)

    build('[content.ratings.a]\nrank=1\nmin_age=13\n[content.ratings.b]\nrank=2\nmin_age=15\n')
    with pytest.raises(ConfigError, match="min_age"):
        build('[content.ratings.a]\nrank=1\nmin_age=15\n[content.ratings.b]\nrank=2\nmin_age=13\n')
    with pytest.raises(ConfigError, match="min_age"):
        build('[content.ratings.a]\nrank=1\nmin_age=16\ngates=true\n')


# -- registration and the review -----------------------------------------------------------------------------
def test_registration_makes_a_viewer_whose_age_is_only_a_claim(store: AuthStore) -> None:
    user = store.register("kid", PASSWORD, years_ago(14), "Kid")
    assert user.role == "viewer" and user.age_status == "pending" and user.age_claim == years_ago(14)
    assert user.age is None and user.birth_date is None and store.age_of(user) == 0 and store.pending_count() == 1


def test_an_admin_approves_the_claim_or_a_corrected_date_and_it_counts_from_then(store: AuthStore) -> None:
    store.register("kid", PASSWORD, years_ago(14))
    uid = store.find_user("kid").id                                              # type: ignore[union-attr]
    approved = store.review_age(uid, approve=True, reviewer="root", birth_date=years_ago(16, 3), note="id checked")
    assert approved.age == 16 and approved.age_status == "approved" and approved.age_reviewed_by == "root"
    assert approved.age_claim == years_ago(14) and store.age_of(approved) == 16 and store.pending_count() == 0
    rejected = store.review_age(uid, approve=False, reviewer="root", note="no")
    assert rejected.age is None and rejected.birth_date is None and rejected.age_status == "rejected"
    again = store.claim_age(uid, years_ago(20))
    assert again.age_status == "pending" and again.age_claim == years_ago(20)
    assert store.review_age(uid, approve=True, reviewer="root").age == 20       # the claim, when none is given


def test_an_approved_age_cannot_be_reclaimed_by_the_person(store: AuthStore) -> None:
    user = store.create_user("grown", PASSWORD, "viewer", birth_date=years_ago(30))
    assert user.age == 30 and user.age_status == "approved"
    with pytest.raises(Exception, match="approved"):
        store.claim_age(user.id, years_ago(40))


def test_registration_can_be_closed_and_the_waiting_list_is_capped(asettings: Settings, store: AuthStore) -> None:
    import dataclasses
    capped = dataclasses.replace(asettings.auth, max_pending_registrations=1)
    s2 = AuthStore(store.db, dataclasses.replace(asettings, auth=capped))
    s2.register("one", PASSWORD, years_ago(20))
    with pytest.raises(RegistrationClosed):
        s2.register("two", PASSWORD, years_ago(20))
    closed = AuthStore(store.db, dataclasses.replace(asettings, auth=dataclasses.replace(
        asettings.auth, allow_registration=False)))
    with pytest.raises(RegistrationClosed):
        closed.register("three", PASSWORD, years_ago(20))


def test_without_accounts_the_operator_is_an_adult(fsettings: Settings) -> None:
    db = Database(fsettings.db_path)
    migrate(db)
    assert AuthStore(db, fsettings).age_of(None) >= 18


# -- the API -------------------------------------------------------------------------------------------------
def make_project(client: TestClient, admin: dict[str, str], name: str, rating: str) -> int:
    r = client.post("/api/projects", json={"name": name, "rating": rating}, headers=admin)
    assert r.status_code == 201, r.text
    return int(r.json()["id"])


def test_register_signs_in_and_shows_a_pending_age(api: tuple[TestClient, AuthStore]) -> None:
    client, _ = api
    assert client.get("/api/auth/mode").json()["allow_registration"] is True
    r = client.post("/api/auth/register", json={"username": "kid", "password": PASSWORD, "birth_date": years_ago(14)})
    assert r.status_code == 201 and r.json()["user"]["age_status"] == "pending" and r.json()["user"]["age"] is None
    me = client.get("/api/auth/me").json()                                       # the cookie carries on
    assert me["user"]["username"] == "kid" and not me["can_write"] and not me["is_admin"]
    dup = client.post("/api/auth/register", json={"username": "kid", "password": PASSWORD, "birth_date": years_ago(20)})
    assert dup.status_code == 409 and dup.json()["error"]["code"] == "user_exists"
    bad = client.post("/api/auth/register", json={"username": "x1", "password": PASSWORD, "birth_date": "2999-01-01"})
    assert bad.status_code == 422 and bad.json()["error"]["code"] == "bad_birth_date"
    weak = client.post("/api/auth/register", json={"username": "x2", "password": "short", "birth_date": years_ago(20)})
    assert weak.status_code == 422


def test_only_an_admin_reviews_ages_and_the_review_is_audited(api: tuple[TestClient, AuthStore]) -> None:
    client, store = api
    store.create_user("root", PASSWORD, "admin", birth_date=years_ago(40))
    client.post("/api/auth/register", json={"username": "kid", "password": PASSWORD, "birth_date": years_ago(14)})
    kid = login(client, "kid")
    uid = store.find_user("kid").id                                              # type: ignore[union-attr]
    assert client.post(f"/api/users/{uid}/age", json={"approve": True}, headers=kid).status_code == 403
    root = login(client, "root")
    listed = {u["username"]: u for u in client.get("/api/users", headers=root).json()}
    assert listed["kid"]["age_status"] == "pending" and listed["kid"]["age_claim"] == years_ago(14)
    r = client.post(f"/api/users/{uid}/age", json={"approve": True, "note": "ok"}, headers=root)
    assert r.status_code == 200 and r.json()["age"] == 14 and r.json()["age_reviewed_by"] == "root"
    actions = [a["action"] for a in client.get("/api/audit", headers=root).json()]
    assert "user.age_approved" in actions and "auth.register" in actions
    assert client.get("/api/auth/me", headers=kid).json()["user"]["age"] == 14   # counts on the next request


def test_a_project_is_closed_to_an_account_below_its_rating_and_open_once_the_age_is_approved(
        api: tuple[TestClient, AuthStore]) -> None:
    client, store = api
    store.create_user("root", PASSWORD, "admin", birth_date=years_ago(40))
    root = login(client, "root")
    pids = {r: make_project(client, root, f"P-{r}", r) for r in ("general", "pg13", "age15")}
    client.post("/api/auth/register", json={"username": "kid", "password": PASSWORD, "birth_date": years_ago(14)})
    kid = login(client, "kid")
    uid = store.find_user("kid").id                                              # type: ignore[union-attr]

    def reachable(headers: dict[str, str]) -> dict[str, bool]:
        return {r: client.get(f"/api/projects/{pid}", headers=headers).status_code == 200 for r, pid in pids.items()}

    def listed(headers: dict[str, str]) -> set[str]:
        return {p["name"] for p in client.get("/api/projects", headers=headers).json()}

    assert reachable(kid) == {"general": True, "pg13": False, "age15": False}   # the age is not approved yet
    denied = client.get(f"/api/projects/{pids['pg13']}", headers=kid)
    assert denied.status_code == 403 and denied.json()["error"]["code"] == "age_restricted"
    assert listed(kid) == {"P-general"} and listed(root) == {"P-general", "P-pg13", "P-age15"}
    client.post(f"/api/users/{uid}/age", json={"approve": True}, headers=root)
    assert reachable(kid) == {"general": True, "pg13": True, "age15": False} and listed(kid) == {"P-general", "P-pg13"}
    client.post(f"/api/users/{uid}/age", json={"approve": True, "birth_date": years_ago(15, 1)}, headers=root)
    assert reachable(kid) == {"general": True, "pg13": True, "age15": True}
    client.post(f"/api/users/{uid}/age", json={"approve": False}, headers=root)
    assert reachable(kid) == {"general": True, "pg13": False, "age15": False}
    denials = client.get("/api/audit", params={"action": "content.age_denied"}, headers=root).json()
    assert denials and denials[0]["detail"]["rating"] in ("pg13", "age15") and denials[0]["actor"] == "kid"


def test_nobody_creates_a_project_rated_above_their_age(api: tuple[TestClient, AuthStore]) -> None:
    client, store = api
    store.create_user("teen", PASSWORD, "producer", birth_date=years_ago(14))
    store.create_user("unsure", PASSWORD, "producer")
    teen, unsure = login(client, "teen"), login(client, "unsure")
    assert client.post("/api/projects", json={"name": "A", "rating": "pg13"}, headers=teen).status_code == 201
    r = client.post("/api/projects", json={"name": "B", "rating": "age15"}, headers=teen)
    assert r.status_code == 403 and r.json()["error"]["code"] == "age_restricted"
    assert client.post("/api/projects", json={"name": "C", "rating": "pg13"}, headers=unsure).status_code == 403
    assert client.post("/api/projects", json={"name": "D"}, headers=unsure).status_code == 201


def test_only_an_approved_adult_can_unlock_restricted_material(api: tuple[TestClient, AuthStore]) -> None:
    client, store = api
    store.create_user("teen", PASSWORD, "producer", birth_date=years_ago(16))
    store.create_user("adult", PASSWORD, "producer", birth_date=years_ago(30))
    store.create_user("unsure", PASSWORD, "producer")
    body: dict[str, Any] = {"confirm_adult": True, "password": PASSWORD}
    for name in ("teen", "unsure"):
        r = client.post("/api/content/unlock", json=body, headers=login(client, name))
        assert r.status_code == 403 and r.json()["error"]["code"] == "age_restricted"
    adult = login(client, "adult")
    assert client.post("/api/content/unlock", json=body, headers=adult).status_code == 200
    status = client.get("/api/content/status", headers=adult).json()
    assert status["unlocked"] and status["viewer_age"] == 30 and status["age_status"] == "approved"
    ratings = {r["name"]: r for r in status["ratings"]}
    assert ratings["pg13"]["min_age"] == 13 and all(r["allowed"] for r in ratings.values())
    teen = client.get("/api/content/status", headers=login(client, "teen")).json()
    assert {n: r["allowed"] for n, r in {r["name"]: r for r in teen["ratings"]}.items()} == {
        "general": True, "pg13": True, "age15": True, "restricted": False}


def test_registration_is_off_when_accounts_are_off_and_reported_so(fsettings: Settings) -> None:
    with TestClient(create_app(fsettings), raise_server_exceptions=False) as c:
        assert c.get("/api/auth/mode").json()["allow_registration"] is False
        r = c.post("/api/auth/register", json={"username": "kid", "password": PASSWORD, "birth_date": years_ago(20)})
        assert r.status_code == 409 and r.json()["error"]["code"] == "auth_off"
        status = c.get("/api/content/status").json()
        assert status["age_status"] == "off" and status["viewer_age"] is None
        assert all(r["allowed"] for r in status["ratings"])


def test_the_first_administrator_is_created_in_the_ui_once(api: tuple[TestClient, AuthStore]) -> None:
    client, _ = api
    assert client.get("/api/auth/mode").json()["first_user"] is True
    r = client.post("/api/auth/setup", json={"username": "root", "password": PASSWORD, "birth_date": years_ago(35)})
    assert r.status_code == 201 and r.json()["user"]["role"] == "admin" and r.json()["user"]["age"] == 35
    me = client.get("/api/auth/me").json()                                       # signed in by the cookie
    assert me["is_admin"] and me["user"]["age_status"] == "approved"
    assert client.get("/api/auth/mode").json()["first_user"] is False
    again = client.post("/api/auth/setup", json={"username": "evil", "password": PASSWORD, "birth_date": years_ago(30)})
    assert again.status_code == 409 and again.json()["error"]["code"] == "setup_done"
