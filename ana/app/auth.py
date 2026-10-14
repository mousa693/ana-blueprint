"""Email + password auth (argon2id) with server-side sessions.

The cookie carries an itsdangerous-signed random token; the server stores only
``sha256(token)`` in ``sessions`` with an expiry, so logout and account
deletion revoke immediately.
"""
from __future__ import annotations

import hashlib
import re
import secrets
from datetime import datetime, timedelta, timezone
from typing import Optional

from argon2 import PasswordHasher
from argon2.exceptions import InvalidHashError, VerificationError, VerifyMismatchError
from itsdangerous import BadSignature, URLSafeTimedSerializer
from sqlalchemy import delete, select
from sqlalchemy.orm import Session

from .models import AuthSession, User, utcnow

COOKIE_NAME = "ana_session"
EMAIL_MAX = 254
PASSWORD_MIN = 10
PASSWORD_MAX = 256
_EMAIL_RE = re.compile(r"^[^@\s\x00-\x1f]{1,64}@[^@\s\x00-\x1f]+\.[^@\s\x00-\x1f]{2,}$")

_hasher = PasswordHasher()   # argon2id, library-default (RFC 9106 low-memory) parameters
_DUMMY_HASH = _hasher.hash("timing-equaliser-not-a-password")


def normalise_email(email: str) -> str:
    return (email or "").strip().lower()


def valid_email(email: str) -> bool:
    return 3 <= len(email) <= EMAIL_MAX and bool(_EMAIL_RE.match(email))


def hash_password(password: str) -> str:
    return _hasher.hash(password)


def verify_password(stored: Optional[str], password: str) -> bool:
    """Constant-ish time: an unknown user still costs one argon2 verify."""
    try:
        return _hasher.verify(stored or _DUMMY_HASH, password) and stored is not None
    except (VerifyMismatchError, VerificationError, InvalidHashError):
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
