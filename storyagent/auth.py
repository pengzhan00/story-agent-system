"""Team access: accounts, sign-in sessions, the permission decision and the audit trail.

* Passwords: salted scrypt (stdlib). Sessions: a random token shown once at login, stored only as its SHA-256.
* ``decide`` is a pure function of the configuration (``[auth]`` in auth.toml): which role may do which request.
* ``AuthStore`` never raises on a missing/expired token, it returns ``None``; the API layer turns that into 401.
* The audit trail is append-only (``audit_log``); request bodies and passwords are never written to it.
"""
from __future__ import annotations

import hashlib
import hmac
import json
import re
import secrets
from collections.abc import Mapping
from dataclasses import dataclass
from datetime import UTC, date, datetime, timedelta
from typing import Any, Literal

from storyagent.config import AuthConfig, Settings
from storyagent.db import Database
from storyagent.util import iso_at, now_iso

SAFE_METHODS = frozenset({"GET", "HEAD", "OPTIONS"})
_USERNAME = re.compile(r"^[A-Za-z0-9_.-]{2,40}$")
_SCRYPT = (2 ** 14, 8, 1)
_PROJECT_ID = re.compile(r"/api/projects/(\d+)")
AgeStatus = Literal["none", "pending", "approved", "rejected"]
_DUMMY_HASH = ""
ADULT_OPERATOR = 1000        # the age of the local operator when accounts are off: no rating is above it


class AuthError(Exception):
    """Base of the account errors; ``code`` is the API error code, ``status`` its HTTP status."""

    code = "invalid"
    status = 422

    def __init__(self, detail: str = "") -> None:
        super().__init__(detail or self.code)
        self.detail = detail


class InvalidCredentials(AuthError):
    code, status = "login_failed", 401


class AccountLocked(AuthError):
    code, status = "account_locked", 429


class UserExists(AuthError):
    code, status = "user_exists", 409


class LastAdmin(AuthError):
    code, status = "last_admin", 409


class WeakPassword(AuthError):
    code, status = "weak_password", 422


class UnknownUser(AuthError, LookupError):
    code, status = "not_found", 404


class RegistrationClosed(AuthError):
    code, status = "registration_closed", 403


class SetupDone(AuthError):
    code, status = "setup_done", 409


class BadBirthDate(AuthError):
    code, status = "bad_birth_date", 422


@dataclass(frozen=True)
class User:
    id: int
    username: str
    display_name: str
    role: str
    active: bool
    last_login_at: str | None
    created_at: str
    birth_date: str | None = None       # only ever set by an admin's approval (or by an admin creating the account)
    age_status: AgeStatus = "none"      # none | pending | approved | rejected
    age_claim: str | None = None        # what the person said; it waits for the review and counts for nothing
    age_note: str = ""
    age_reviewed_by: str | None = None
    age_reviewed_at: str | None = None

    @property
    def age(self) -> int | None:
        """Full years today, from the approved birth date; ``None`` while no age is approved."""
        return age_on(self.birth_date) if self.age_status == "approved" else None


def _user(row: Any) -> User:
    return User(row["id"], row["username"], row["display_name"], row["role"], bool(row["active"]),
                row["last_login_at"], row["created_at"], row["birth_date"], row["age_status"], row["age_claim"],
                row["age_note"], row["age_reviewed_by"], row["age_reviewed_at"])


# -- age --------------------------------------------------------------------------------------------------
def parse_birth_date(text: str, today: date | None = None) -> date:
    """``YYYY-MM-DD``, not in the future and not older than 120 years."""
    today = today or datetime.now(UTC).date()
    try:
        born = date.fromisoformat(text.strip())
    except ValueError:
        raise BadBirthDate("YYYY-MM-DD") from None
    if born > today or (today - born).days > 120 * 366:
        raise BadBirthDate(text)
    return born


def age_on(birth_date: str | None, today: date | None = None) -> int | None:
    """Full years on ``today`` (default: today); it grows by itself, only the birth date is stored."""
    if not birth_date:
        return None
    today = today or datetime.now(UTC).date()
    try:
        born = date.fromisoformat(birth_date)
    except ValueError:
        return None
    return today.year - born.year - ((today.month, today.day) < (born.month, born.day))


# -- passwords --------------------------------------------------------------------------------------------
def hash_password(password: str) -> str:
    n, r, p = _SCRYPT
    salt = secrets.token_bytes(16)
    digest = hashlib.scrypt(password.encode(), salt=salt, n=n, r=r, p=p, dklen=32)
    return f"scrypt${n}${r}${p}${salt.hex()}${digest.hex()}"


def verify_password(password: str, stored: str) -> bool:
    try:
        scheme, n, r, p, salt, digest = stored.split("$")
        if scheme != "scrypt":
            return False
        got = hashlib.scrypt(password.encode(), salt=bytes.fromhex(salt), n=int(n), r=int(r), p=int(p), dklen=32)
        return hmac.compare_digest(got.hex(), digest)
    except (ValueError, TypeError):
        return False


def _token_hash(token: str) -> str:
    return hashlib.sha256(token.encode()).hexdigest()


# -- the permission decision ------------------------------------------------------------------------------
def _starts(path: str, prefixes: tuple[str, ...]) -> bool:
    return any(path.startswith(p) for p in prefixes)


# Hard deletes (file and record gone for good) are an admin's decision, whatever the role's ``write`` flag says.
_HARD_DELETE = re.compile(r"/api/assets/[^/]+/purge(-version)?")


def decide(cfg: AuthConfig, user: User | None, method: str, path: str) -> str | None:
    """``None`` = allowed, else the API error code: ``unauthorized`` (sign in first) or ``forbidden`` (role too low).

    Anything outside /api (the web client's files) and the public prefixes needs no sign-in. Signed in: reading is
    always allowed; the signed-in prefixes (own password, logout) need nothing more; admin prefixes need the admin
    flag; creating/removing a project needs ``create_project``; every other write needs ``write``."""
    if not cfg.enabled or not path.startswith("/api/") or _starts(path, cfg.public_prefixes):
        return None
    if user is None:
        return "unauthorized"
    role = cfg.roles.get(user.role)
    if role is None:
        return "forbidden"
    if _starts(path, cfg.signed_in_prefixes):
        return None
    if _starts(path, cfg.admin_prefixes) or _HARD_DELETE.fullmatch(path):
        return None if role.admin else "forbidden"
    if method in SAFE_METHODS:
        return None
    for prefix in cfg.project_prefixes:
        base = re.escape(prefix)
        is_project_op = ((method == "POST" and re.fullmatch(rf"{base}(/from-text|/import-pack|/\d+/restore)?", path))
                         or (method == "DELETE" and re.fullmatch(rf"{base}/\d+", path)))
        if is_project_op:
            return None if role.create_project else "forbidden"
    return None if role.write else "forbidden"


def project_of(path: str) -> int | None:
    m = _PROJECT_ID.match(path)
    return int(m.group(1)) if m else None


class AuthStore:
    def __init__(self, db: Database, settings: Settings) -> None:
        self.db = db
        self.settings = settings

    @property
    def cfg(self) -> AuthConfig:
        return self.settings.auth

    # -- accounts -----------------------------------------------------------------------------------------
    def _check_password(self, password: str) -> None:
        if len(password) < self.cfg.min_password_length:
            raise WeakPassword(f"at least {self.cfg.min_password_length} characters")

    def _check_role(self, role: str) -> None:
        if role not in self.cfg.roles:
            raise AuthError(f"unknown role {role!r}; known: {sorted(self.cfg.roles)}")

    def create_user(self, username: str, password: str, role: str, display_name: str = "",
                    birth_date: str = "") -> User:
        """An account made by an admin (or the CLI). With ``birth_date`` the age counts at once (the admin vouches)."""
        username = username.strip()
        if not _USERNAME.match(username):
            raise AuthError("username: 2-40 letters, digits, '.', '_' or '-'")
        self._check_role(role)
        self._check_password(password)
        born = parse_birth_date(birth_date).isoformat() if birth_date.strip() else None
        if self.db.conn.execute("SELECT 1 FROM users WHERE username=?", (username,)).fetchone():
            raise UserExists(username)
        cur = self.db.conn.execute(
            "INSERT INTO users(username, display_name, role, password_hash, birth_date, age_status, age_reviewed_by,"
            " age_reviewed_at) VALUES (?,?,?,?,?,?,?,?)",
            (username, display_name.strip(), role, hash_password(password), born, "approved" if born else "none",
             "admin" if born else None, now_iso() if born else None),
        )
        assert cur.lastrowid is not None
        return self.get_user(cur.lastrowid)

    # -- registration and the age review ------------------------------------------------------------------
    def register(self, username: str, password: str, birth_date: str, display_name: str = "") -> User:
        """Self-registration: a ``registration_role`` account whose age is only a claim until an admin approves it.
        The number of waiting requests is capped (``max_pending_registrations``)."""
        if not self.cfg.allow_registration:
            raise RegistrationClosed()
        claim = parse_birth_date(birth_date).isoformat()
        if self.pending_count() >= self.cfg.max_pending_registrations:
            raise RegistrationClosed("too many requests are waiting for a review")
        user = self.create_user(username, password, self.cfg.registration_role, display_name)
        self.db.conn.execute("UPDATE users SET age_status='pending', age_claim=? WHERE id=?", (claim, user.id))
        return self.get_user(user.id)

    def setup_admin(self, username: str, password: str, birth_date: str, display_name: str = "") -> User:
        """The first account of a fresh install: an admin whose age counts at once (the person setting up vouches for
        themself). Only while no account exists."""
        with self.db.transaction() as conn:
            if int(conn.execute("SELECT count(*) FROM users").fetchone()[0]) > 0:
                raise SetupDone()
            admin = next((n for n, r in self.cfg.roles.items() if r.admin), "admin")
            return self.create_user(username, password, admin, display_name, birth_date)

    def pending_count(self) -> int:
        return int(self.db.conn.execute("SELECT count(*) FROM users WHERE age_status='pending'").fetchone()[0])

    def claim_age(self, user_id: int, birth_date: str) -> User:
        """Say (again) when you were born; an approved age is changed by an admin only."""
        current = self.get_user(user_id)
        if current.age_status == "approved":
            raise AuthError("the age is already approved; an admin changes it")
        claim = parse_birth_date(birth_date).isoformat()
        self.db.conn.execute("UPDATE users SET age_status='pending', age_claim=?, age_note='' WHERE id=?",
                             (claim, user_id))
        return self.get_user(user_id)

    def review_age(self, user_id: int, *, approve: bool, reviewer: str, birth_date: str = "", note: str = "") -> User:
        """Approve (the claimed birth date, or the one the admin corrects it to) or reject the age of an account.
        Sessions stay valid; the new age counts on the next request."""
        user = self.get_user(user_id)
        values: dict[str, Any] = {"age_status": "approved" if approve else "rejected", "age_note": note.strip()[:300],
                                  "age_reviewed_by": reviewer, "age_reviewed_at": now_iso()}
        if approve:
            chosen = birth_date.strip() or user.age_claim or user.birth_date or ""
            if not chosen:
                raise BadBirthDate("no birth date to approve")
            values["birth_date"] = parse_birth_date(chosen).isoformat()
        else:
            values["birth_date"] = None
        marks = ", ".join(f"{k}=?" for k in values)
        self.db.conn.execute(f"UPDATE users SET {marks} WHERE id=?", (*values.values(), user_id))
        return self.get_user(user_id)

    def age_of(self, user: User | None) -> int:
        """The age that counts for what this caller may see. Without accounts (auth off) the operator is an adult;
        with accounts an unapproved age is 0."""
        if not self.cfg.enabled:
            return ADULT_OPERATOR
        return (user.age or 0) if user else 0

    def get_user(self, user_id: int) -> User:
        row = self.db.conn.execute("SELECT * FROM users WHERE id=?", (user_id,)).fetchone()
        if row is None:
            raise UnknownUser(f"unknown user {user_id}")
        return _user(row)

    def find_user(self, username: str) -> User | None:
        row = self.db.conn.execute("SELECT * FROM users WHERE username=?", (username.strip(),)).fetchone()
        return _user(row) if row else None

    def list_users(self) -> list[User]:
        return [_user(r) for r in self.db.conn.execute("SELECT * FROM users ORDER BY username").fetchall()]

    def count_users(self) -> int:
        return int(self.db.conn.execute("SELECT count(*) FROM users").fetchone()[0])

    def _active_admins(self, excluding: int | None = None) -> int:
        admin_roles = [n for n, r in self.cfg.roles.items() if r.admin]
        marks = ",".join("?" * len(admin_roles))
        row = self.db.conn.execute(
            f"SELECT count(*) FROM users WHERE active=1 AND role IN ({marks}) AND id IS NOT ?",
            (*admin_roles, excluding)).fetchone()
        return int(row[0])

    def update_user(self, user_id: int, *, role: str | None = None, active: bool | None = None,
                    display_name: str | None = None, password: str | None = None) -> User:
        current = self.get_user(user_id)
        sets: dict[str, Any] = {}
        if role is not None:
            self._check_role(role)
            sets["role"] = role
        if active is not None:
            sets["active"] = int(active)
        if display_name is not None:
            sets["display_name"] = display_name.strip()
        if password is not None:
            self._check_password(password)
            sets["password_hash"] = hash_password(password)
            sets["failed_logins"], sets["locked_until"] = 0, None
        old_role = self.cfg.roles.get(current.role)
        new_role = self.cfg.roles[sets.get("role", current.role)] if sets.get("role", current.role) in self.cfg.roles \
            else None
        was_admin = current.active and old_role is not None and old_role.admin
        will_admin = bool(sets.get("active", current.active)) and new_role is not None and new_role.admin
        if was_admin and not will_admin and self._active_admins(excluding=user_id) == 0:
            raise LastAdmin("the last active admin cannot be demoted or deactivated")
        if sets:
            marks = ", ".join(f"{k}=?" for k in (*sets, "updated_at"))
            self.db.conn.execute(f"UPDATE users SET {marks} WHERE id=?", (*sets.values(), now_iso(), user_id))
        if password is not None or active is False:
            self.revoke_all(user_id)
        return self.get_user(user_id)

    # -- sessions -----------------------------------------------------------------------------------------
    def login(self, username: str, password: str, *, label: str = "") -> tuple[str, User, str]:
        """Check the credentials and open a session: ``(token, user, expires_at)``. Repeated failures lock the account
        for ``lockout_minutes``; an unknown name costs as much time as a wrong password."""
        global _DUMMY_HASH
        row = self.db.conn.execute("SELECT * FROM users WHERE username=?", (username.strip(),)).fetchone()
        if row is None:
            _DUMMY_HASH = _DUMMY_HASH or hash_password("x")
            verify_password(password, _DUMMY_HASH)
            raise InvalidCredentials()
        now = datetime.now(UTC)
        if row["locked_until"] and row["locked_until"] > now_iso():
            raise AccountLocked(row["locked_until"])
        if not row["active"] or not verify_password(password, row["password_hash"]):
            failures = int(row["failed_logins"]) + 1
            locked = None
            if failures >= self.cfg.login_max_failures:
                locked = iso_at((now + timedelta(minutes=self.cfg.lockout_minutes)).timestamp())
                failures = 0
            self.db.conn.execute("UPDATE users SET failed_logins=?, locked_until=? WHERE id=?",
                                 (failures, locked, row["id"]))
            raise InvalidCredentials()
        token = secrets.token_urlsafe(32)
        expires = iso_at((now + timedelta(hours=self.cfg.session_hours)).timestamp())
        with self.db.transaction() as conn:
            conn.execute("UPDATE users SET failed_logins=0, locked_until=NULL, last_login_at=? WHERE id=?",
                         (now_iso(), row["id"]))
            conn.execute("INSERT INTO user_sessions(user_id, token_hash, label, expires_at) VALUES (?,?,?,?)",
                         (row["id"], _token_hash(token), label[:80], expires))
        return token, self.get_user(row["id"]), expires

    def authenticate(self, token: str) -> User | None:
        row = self.db.conn.execute(
            "SELECT u.*, s.id AS sid, s.last_used_at AS used FROM user_sessions s JOIN users u ON u.id=s.user_id "
            "WHERE s.token_hash=? AND s.revoked_at IS NULL AND s.expires_at>? AND u.active=1",
            (_token_hash(token), now_iso())).fetchone()
        if row is None:
            return None
        if not row["used"] or row["used"] < iso_at(datetime.now(UTC).timestamp() - 60):   # at most one write a minute
            self.db.conn.execute("UPDATE user_sessions SET last_used_at=? WHERE id=?", (now_iso(), row["sid"]))
        return _user(row)

    def logout(self, token: str) -> None:
        self.db.conn.execute("UPDATE user_sessions SET revoked_at=? WHERE token_hash=? AND revoked_at IS NULL",
                             (now_iso(), _token_hash(token)))

    def revoke_all(self, user_id: int) -> None:
        self.db.conn.execute("UPDATE user_sessions SET revoked_at=? WHERE user_id=? AND revoked_at IS NULL",
                             (now_iso(), user_id))

    def change_password(self, user_id: int, old: str, new: str) -> None:
        row = self.db.conn.execute("SELECT password_hash FROM users WHERE id=?", (user_id,)).fetchone()
        if row is None or not verify_password(old, row["password_hash"]):
            raise InvalidCredentials()
        self.update_user(user_id, password=new)

    # -- audit --------------------------------------------------------------------------------------------
    def audit(self, action: str, *, user: User | None = None, actor: str = "", path: str = "",
              project_id: int | None = None, status: int | None = None, detail: Mapping[str, Any] | None = None,
              client: str = "") -> None:
        self.db.conn.execute(
            "INSERT INTO audit_log(user_id, actor, action, path, project_id, status, detail, client) "
            "VALUES (?,?,?,?,?,?,?,?)",
            (user.id if user else None, user.username if user else actor, action, path, project_id, status,
             json.dumps(dict(detail or {}), ensure_ascii=False), client[:80]))

    def list_audit(self, *, user: str = "", action: str = "", project_id: int | None = None, since: str = "",
                   limit: int = 100) -> list[dict[str, Any]]:
        where, args = [], []
        for col, val in (("actor", user), ("action", action), ("project_id", project_id)):
            if val not in ("", None):
                where.append(f"{col}=?")
                args.append(val)
        if since:
            where.append("at>=?")
            args.append(since)
        sql = "SELECT * FROM audit_log" + (" WHERE " + " AND ".join(where) if where else "") + \
              " ORDER BY id DESC LIMIT ?"
        rows = self.db.conn.execute(sql, (*args, limit)).fetchall()
        return [{**dict(r), "detail": json.loads(r["detail"] or "{}")} for r in rows]
