"""Email + password auth (argon2id) with server-side sessions.

The cookie carries an itsdangerous-signed random token; the server stores only
``sha256(token)`` in ``sessions`` with an expiry, so logout and account
deletion revoke immediately.
"""
from __future__ import annotations

import hashlib
import re
import secrets
import threading
import unicodedata
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timedelta, timezone
from typing import Optional

from argon2 import PasswordHasher, Type
from argon2.exceptions import InvalidHashError, VerificationError, VerifyMismatchError
from itsdangerous import BadSignature, URLSafeTimedSerializer
from sqlalchemy import delete, select
from sqlalchemy.orm import Session

from .models import AuthSession, User, utcnow
from .security import ApiError

COOKIE_NAME = "ana_session"
EMAIL_MAX = 254
PASSWORD_MIN = 10
PASSWORD_MAX = 256
# Local part: the RFC 5322 "atext" set plus dots (ASCII only — no emoji,
# control or invisible characters). Domain: LDH labels after IDNA encoding.
_LOCAL_RE = re.compile(r"^[A-Za-z0-9!#$%&'*+/=?^_`{|}~.-]{1,64}$")
_LABEL_RE = re.compile(r"^(?!-)[a-z0-9-]{1,63}(?<!-)$")

# argon2id at the OWASP Password Storage Cheat Sheet minimum (m=19 MiB, t=2,
# p=1) instead of the library default (m=64 MiB, t=3, p=4): one hash costs
# ~19 MiB of RAM, so the bounded number of concurrent hashes below fits a
# 512 MB VM. Hashes made with other parameters still verify and are rehashed
# with these on the next successful login.
ARGON2_TIME_COST = 2
ARGON2_MEMORY_KIB = 19456
ARGON2_PARALLELISM = 1
_hasher = PasswordHasher(time_cost=ARGON2_TIME_COST, memory_cost=ARGON2_MEMORY_KIB,
                         parallelism=ARGON2_PARALLELISM, hash_len=32, salt_len=16,
                         type=Type.ID)
_DUMMY_HASH = _hasher.hash("timing-equaliser-not-a-password")

# Process-wide cap on concurrent argon2 work (memory is the constraint).
# The work runs on a dedicated pool of ``concurrency`` threads, not on the
# request threads: glibc gives each thread its own malloc arena and, once a
# 19 MiB block has been freed, keeps later blocks of that size in the arena
# instead of returning them — 40 request threads would each end up holding
# one (measured: ~460 MB RSS after 20 parallel logins). With a fixed pool at
# most ``concurrency`` arenas ever hold argon2 memory.
_hash_slots = threading.BoundedSemaphore(2)
_hash_pool = ThreadPoolExecutor(max_workers=2, thread_name_prefix="ana-argon2")
_hash_n = 2
_hash_wait = 2.0
_hash_cfg_lock = threading.Lock()


def configure_hashing(concurrency: int, wait_seconds: float) -> None:
    """Set the process-wide argon2 concurrency (called by ``create_app``)."""
    global _hash_slots, _hash_pool, _hash_n, _hash_wait
    concurrency = max(1, int(concurrency))
    with _hash_cfg_lock:
        if concurrency != _hash_n:
            old = _hash_pool
            _hash_slots = threading.BoundedSemaphore(concurrency)
            _hash_pool = ThreadPoolExecutor(max_workers=concurrency,
                                            thread_name_prefix="ana-argon2")
            _hash_n = concurrency
            old.shutdown(wait=False)
        _hash_wait = max(0.0, float(wait_seconds))


def _hash_busy() -> ApiError:
    return ApiError(503, "busy", "We're busy right now. Please try again in a moment.",
                    headers={"Retry-After": "2"})


def _run_hash(fn, *args):
    """Run argon2 work on the hashing pool; 503 when no slot frees up within
    the wait (fail fast rather than queue unboundedly)."""
    sem, pool = _hash_slots, _hash_pool
    if not sem.acquire(timeout=_hash_wait):
        raise _hash_busy()
    try:
        return pool.submit(fn, *args).result()
    finally:
        sem.release()


def normalise_email(email: str) -> str:
    """Trim, lowercase, and IDNA-encode a non-ASCII domain (so a look-alike
    Unicode domain is a different, punycode address). Returns the input
    lowercased when it cannot be normalised; :func:`valid_email` rejects it."""
    e = (email or "").strip().lower()
    if "@" in e:
        local, _, domain = e.rpartition("@")
        if domain and not domain.isascii():
            # only letters/marks/digits may be non-ASCII: no invisible, format,
            # symbol (emoji) or space characters that IDNA would silently drop
            if any(not ch.isascii() and unicodedata.category(ch)[0] not in "LMN"
                   for ch in domain):
                return e
            try:
                domain = domain.encode("idna").decode("ascii").lower()
            except (UnicodeError, ValueError):
                return e
            e = f"{local}@{domain}"
    return e


def valid_email(email: str) -> bool:
    """For an already :func:`normalise_email`-ed address: ASCII only, a sane
    local part and a dotted LDH domain with a 2+ letter top-level label."""
    if not (3 <= len(email) <= EMAIL_MAX) or not email.isascii() or email.count("@") != 1:
        return False
    local, domain = email.split("@")
    if not _LOCAL_RE.match(local) or local.startswith(".") or local.endswith(".") \
            or ".." in local:
        return False
    labels = domain.split(".")
    if len(labels) < 2 or not all(_LABEL_RE.match(lb) for lb in labels):
        return False
    tld = labels[-1]
    return len(tld) >= 2 and (tld.isalpha() or tld.startswith("xn--"))


def password_ok(password: str) -> bool:
    """At least PASSWORD_MIN characters not counting spaces at either end."""
    return len(password.strip()) >= PASSWORD_MIN


def hash_password(password: str) -> str:
    return _run_hash(_hasher.hash, password)


def burn_hash(password: str) -> None:
    """Spend the same time as a real sign-up (account-existence timing)."""
    hash_password(password)


def _verify(stored: Optional[str], password: str) -> bool:
    try:
        return _hasher.verify(stored or _DUMMY_HASH, password) and stored is not None
    except (VerifyMismatchError, VerificationError, InvalidHashError):
        return False


def verify_password(stored: Optional[str], password: str) -> bool:
    """Constant-ish time: an unknown user still costs one argon2 verify."""
    return _run_hash(_verify, stored, password)


def needs_rehash(stored: str) -> bool:
    try:
        return _hasher.check_needs_rehash(stored)
    except (InvalidHashError, ValueError):
        return False


def aware(dt: datetime) -> datetime:
    return dt if dt.tzinfo else dt.replace(tzinfo=timezone.utc)


class SessionManager:
    def __init__(self, secret_key: str, days: int = 30):
        self.serializer = URLSafeTimedSerializer(secret_key, salt="ana.session.v1")
        self.max_age = days * 86400
        self.days = days

    @staticmethod
    def _sid(token: str) -> str:
        return hashlib.sha256(token.encode()).hexdigest()

    def create(self, db: Session, user: User) -> str:
        """Persist a new session; returns the signed cookie value."""
        token = secrets.token_urlsafe(32)
        db.add(AuthSession(id=self._sid(token), user_id=user.id, created_at=utcnow(),
                           expires_at=utcnow() + timedelta(days=self.days)))
        return self.serializer.dumps(token)

    def _token(self, cookie: Optional[str]) -> Optional[str]:
        if not cookie or len(cookie) > 512:
            return None
        try:
            token = self.serializer.loads(cookie, max_age=self.max_age)
        except BadSignature:
            return None
        return token if isinstance(token, str) else None

    def user_for(self, db: Session, cookie: Optional[str]) -> Optional[User]:
        token = self._token(cookie)
        if token is None:
            return None
        row = db.get(AuthSession, self._sid(token))
        if row is None:
            return None
        if aware(row.expires_at) <= utcnow():
            db.delete(row)
            db.commit()
            return None
        user = db.get(User, row.user_id)
        if user is None or user.deleted_at is not None:
            return None
        return user

    def revoke(self, db: Session, cookie: Optional[str]) -> None:
        token = self._token(cookie)
        if token is not None:
            db.execute(delete(AuthSession).where(AuthSession.id == self._sid(token)))

    @staticmethod
    def revoke_all(db: Session, user_id: str) -> None:
        db.execute(delete(AuthSession).where(AuthSession.user_id == user_id))

    @staticmethod
    def purge_expired(db: Session) -> int:
        res = db.execute(delete(AuthSession).where(AuthSession.expires_at <= utcnow()))
        return res.rowcount or 0


def find_user(db: Session, email: str) -> Optional[User]:
    return db.execute(select(User).where(User.email == email)).scalars().first()
