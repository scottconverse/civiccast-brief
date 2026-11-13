"""Sign-in for the editor desk, ported from TownReporter's sign-in.

TownReporter runs Better Auth with email and password. This module keeps its rules:

  - the first account made on a desk owns it; after that, sign-up is closed and the page says
    "This desk already has an editor. Sign in if that's you.";
  - passwords are hashed with scrypt (N=16384, r=16, p=1, 64-byte key, 16-byte hex salt),
    stored as ``salt:key`` in hex, the same format Better Auth writes;
  - a password is 8 to 128 characters;
  - a session lasts 7 days and is renewed when it is used more than a day after it was made
    or last renewed. Only a SHA-256 of the session token is stored, so the database alone
    cannot sign anyone in;
  - the session cookie is ``__Host-`` prefixed, Secure, HttpOnly, SameSite=Lax and Path=/. A
    browser refuses a same-named cookie that carries a Domain, so a sibling site on the same
    parent domain cannot plant one;
  - sign-in is throttled by the visitor's address (10 tries per 5 minutes; sign-up 5 per
    hour), and each account locks after 10 wrong passwords for 15 minutes. The account lock
    does not depend on any header the caller sends. It blocks the right password too while it
    holds, tries made while it holds do not extend it, and a right password clears it.

The counters live in memory, as in TownReporter, so a restart clears them.
"""

from __future__ import annotations

import hashlib
import hmac
import secrets
import sqlite3
import threading
import time
import unicodedata
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path
from typing import Final

SESSION_COOKIE: Final = "__Host-ccb.session_token"
SESSION_SECONDS: Final = 7 * 24 * 3600
RENEW_AFTER_SECONDS: Final = 24 * 3600
MIN_PASSWORD: Final = 8
MAX_PASSWORD: Final = 128
MAX_EMAIL: Final = 254
CLOSED_MESSAGE: Final = "This desk already has an editor. Sign in if that's you."
WRONG_MESSAGE: Final = "The email or password is wrong."
LOCKED_MESSAGE: Final = "Too many failed sign-in attempts for this account. Try again later."
THROTTLED_MESSAGE: Final = "Too many sign-in attempts from this address. Try again later."

_SCRYPT_N: Final = 16384
_SCRYPT_R: Final = 16
_SCRYPT_P: Final = 1
_SCRYPT_KEY: Final = 64

Clock = Callable[[], float]

_SCHEMA: Final = """
CREATE TABLE IF NOT EXISTS desk_users (
    id TEXT PRIMARY KEY,
    email TEXT NOT NULL UNIQUE,
    password TEXT NOT NULL,
    role TEXT NOT NULL,
    created_at REAL NOT NULL
);
CREATE TABLE IF NOT EXISTS desk_sessions (
    token_hash TEXT PRIMARY KEY,
    user_id TEXT NOT NULL REFERENCES desk_users (id) ON DELETE CASCADE,
    expires_at REAL NOT NULL,
    renewed_at REAL NOT NULL
);
CREATE INDEX IF NOT EXISTS desk_sessions_user ON desk_sessions (user_id);
"""


class AuthError(Exception):
    """A refused sign-in or sign-up. ``status`` is the HTTP status the desk answers with."""

    def __init__(self, message: str, status: int = 400, retry_after: int | None = None) -> None:
        super().__init__(message)
        self.status = status
        self.retry_after = retry_after


@dataclass(frozen=True)
class DeskUser:
    id: str
    email: str
    role: str


def _normalise(password: str) -> bytes:
    return unicodedata.normalize("NFKC", password).encode("utf-8")


def _scrypt(password: str, salt: str) -> bytes:
    return hashlib.scrypt(
        _normalise(password),
        salt=salt.encode("utf-8"),
        n=_SCRYPT_N,
        r=_SCRYPT_R,
        p=_SCRYPT_P,
        dklen=_SCRYPT_KEY,
        maxmem=64 * 1024 * 1024,
    )


def hash_password(password: str) -> str:
    """``salt:key`` in hex, as Better Auth stores it."""
    salt = secrets.token_hex(16)
    return f"{salt}:{_scrypt(password, salt).hex()}"


def verify_password(stored: str, password: str) -> bool:
    salt, _, key = stored.partition(":")
    if not salt or not key:
        return False
    return hmac.compare_digest(_scrypt(password, salt).hex(), key)


def normalise_email(email: str) -> str:
    """Trim and lowercase, so "Editor@Example.com " and "editor@example.com" are one account."""
    return email.strip().lower()


def _token_hash(token: str) -> str:
    return hashlib.sha256(token.encode("utf-8")).hexdigest()


class AccountLockout:
    """Per-account lock: ``max_attempts`` wrong passwords within ``window`` seconds."""

    def __init__(self, max_attempts: int = 10, window: int = 900, clock: Clock = time.time):
        self.max_attempts = max_attempts
        self.window = window
        self._now = clock
        self._store: dict[str, tuple[int, float]] = {}

    def _expired(self, last: float) -> bool:
        return self._now() - last > self.window

    def retry_after(self, email: str) -> int | None:
        """Seconds until the account may try again, or None when it is not locked."""
        entry = self._store.get(normalise_email(email))
        if entry is None or self._expired(entry[1]) or entry[0] < self.max_attempts:
            return None
        return max(1, int(-(-(self.window - (self._now() - entry[1])) // 1)))

    def failure(self, email: str) -> None:
        key = normalise_email(email)
        entry = self._store.get(key)
        if entry is None or self._expired(entry[1]):
            self._store[key] = (1, self._now())
        else:
            self._store[key] = (entry[0] + 1, self._now())

    def success(self, email: str) -> None:
        self._store.pop(normalise_email(email), None)


class AddressThrottle:
    """At most ``limit`` tries per ``window`` seconds from one address."""

    def __init__(self, limit: int, window: int, clock: Clock = time.time) -> None:
        self.limit = limit
        self.window = window
        self._now = clock
        self._hits: dict[str, list[float]] = {}

    def take(self, address: str) -> int | None:
        """Count one try. Seconds to wait when over the limit, else None."""
        now = self._now()
        hits = [t for t in self._hits.get(address, []) if now - t < self.window]
        if len(hits) >= self.limit:
            self._hits[address] = hits
            return max(1, int(-(-(self.window - (now - hits[0])) // 1)))
        hits.append(now)
        self._hits[address] = hits
        return None


class DeskAuth:
    """Accounts and sessions in the desk's SQLite file (tables ``desk_users``, ``desk_sessions``)."""

    def __init__(self, path: str | Path, *, clock: Clock = time.time) -> None:
        self._now = clock
        self._lock = threading.RLock()
        self._conn = sqlite3.connect(
            Path(path), isolation_level=None, timeout=10, check_same_thread=False
        )
        self._conn.row_factory = sqlite3.Row
        self._conn.execute("PRAGMA foreign_keys = ON")
        self._conn.executescript(_SCHEMA)
        self.lockout = AccountLockout(clock=clock)
        self.sign_in_throttle = AddressThrottle(10, 300, clock=clock)
        self.sign_up_throttle = AddressThrottle(5, 3600, clock=clock)
        # Hashed on first need, so a sign-in for an unknown email costs the same as a wrong
        # password.
        self._dummy = ""

    def close(self) -> None:
        with self._lock:
            self._conn.close()

    def has_owner(self) -> bool:
        with self._lock:
            return self._conn.execute("SELECT 1 FROM desk_users LIMIT 1").fetchone() is not None

    def create_owner(self, email: str, password: str, address: str) -> str:
        """Make the desk's first account and sign it in. Returns the session token."""
        wait = self.sign_up_throttle.take(address)
        if wait is not None:
            raise AuthError(THROTTLED_MESSAGE, 429, wait)
        email = normalise_email(email)
        if "@" not in email or len(email) > MAX_EMAIL:
            raise AuthError("Enter an email address.")
        if not MIN_PASSWORD <= len(password) <= MAX_PASSWORD:
            raise AuthError(f"The password must be {MIN_PASSWORD} to {MAX_PASSWORD} characters.")
        hashed = hash_password(password)
        user_id = secrets.token_urlsafe(16)
        with self._lock:
            conn = self._conn
            conn.execute("BEGIN IMMEDIATE")
            try:
                if conn.execute("SELECT 1 FROM desk_users LIMIT 1").fetchone() is not None:
                    raise AuthError(CLOSED_MESSAGE, 403)
                conn.execute(
                    "INSERT INTO desk_users (id, email, password, role, created_at)"
                    " VALUES (?, ?, ?, 'owner', ?)",
                    (user_id, email, hashed, self._now()),
                )
                token = self._new_session(user_id)
                conn.execute("COMMIT")
            except BaseException:
                conn.execute("ROLLBACK")
                raise
        return token

    def sign_in(self, email: str, password: str, address: str) -> str:
        """Check the password and start a session. Returns the session token."""
        wait = self.sign_in_throttle.take(address)
        if wait is not None:
            raise AuthError(THROTTLED_MESSAGE, 429, wait)
        email = normalise_email(email)
        locked = self.lockout.retry_after(email)
        if locked is not None:
            raise AuthError(LOCKED_MESSAGE, 429, locked)
        with self._lock:
            row = self._conn.execute(
                "SELECT id, password FROM desk_users WHERE email = ?", (email,)
            ).fetchone()
        if row is None:
            self._dummy = self._dummy or hash_password(secrets.token_hex(8))
            verify_password(self._dummy, password)
            ok = False
        else:
            ok = verify_password(row["password"], password)
        if self.lockout.retry_after(email) is not None:
            raise AuthError(LOCKED_MESSAGE, 429, self.lockout.retry_after(email))
        if not ok or row is None:
            self.lockout.failure(email)
            raise AuthError(WRONG_MESSAGE, 401)
        self.lockout.success(email)
        with self._lock:
            return self._new_session(row["id"])

    def _new_session(self, user_id: str) -> str:
        token = secrets.token_urlsafe(32)
        now = self._now()
        self._conn.execute(
            "INSERT INTO desk_sessions (token_hash, user_id, expires_at, renewed_at)"
            " VALUES (?, ?, ?, ?)",
            (_token_hash(token), user_id, now + SESSION_SECONDS, now),
        )
        return token

    def user_for(self, token: str | None) -> DeskUser | None:
        """The signed-in account for a session token, or None. Renews a day-old session."""
        if not token:
            return None
        key = _token_hash(token)
        now = self._now()
        with self._lock:
            row = self._conn.execute(
                "SELECT s.expires_at, s.renewed_at, u.id, u.email, u.role FROM desk_sessions s"
                " JOIN desk_users u ON u.id = s.user_id WHERE s.token_hash = ?",
                (key,),
            ).fetchone()
            if row is None:
                return None
            if row["expires_at"] <= now:
                self._conn.execute("DELETE FROM desk_sessions WHERE token_hash = ?", (key,))
                return None
            if now - row["renewed_at"] > RENEW_AFTER_SECONDS:
                self._conn.execute(
                    "UPDATE desk_sessions SET expires_at = ?, renewed_at = ? WHERE token_hash = ?",
                    (now + SESSION_SECONDS, now, key),
                )
        return DeskUser(id=row["id"], email=row["email"], role=row["role"])

    def sign_out(self, token: str | None) -> None:
        if not token:
            return
        with self._lock:
            self._conn.execute(
                "DELETE FROM desk_sessions WHERE token_hash = ?", (_token_hash(token),)
            )
