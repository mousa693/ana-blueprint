"""Configuration from the environment (docs/PRODUCT_SPEC.md §3).

``Settings.from_env()`` reads the documented variables; tests build a
``Settings`` directly (or call ``from_env`` with a dict). A handful of extra,
undocumented knobs exist for operations and tests (``ANA_MAX_CYCLES``,
``ANA_RUNTIME_CACHE_SIZE``, rate-limit sizes); their defaults are the product
defaults.
"""
from __future__ import annotations

import logging
import os
import secrets
from dataclasses import dataclass, field, fields, replace
from typing import Mapping, Optional
from urllib.parse import urlsplit, urlunsplit

logger = logging.getLogger("ana.app")


class ConfigError(RuntimeError):
    """The environment does not describe a runnable server."""


def _bool(v: Optional[str], default: bool) -> bool:
    if v is None or v.strip() == "":
        return default
    return v.strip().lower() in ("1", "true", "yes", "on")


def _int(env: Mapping[str, str], name: str, default: int, lo: int = 0) -> int:
    raw = env.get(name)
    if raw is None or raw.strip() == "":
        return default
    try:
        v = int(raw.strip())
    except ValueError:
        raise ConfigError(f"{name} must be an integer (got {raw!r})") from None
    if v < lo:
        raise ConfigError(f"{name} must be >= {lo}")
    return v


def _csv(v: Optional[str]) -> list[str]:
    return [p.strip() for p in (v or "").split(",") if p.strip()]


def normalise_db_url(url: str) -> str:
    """``postgres://`` / ``postgresql://`` -> the psycopg 3 driver."""
    if url.startswith("postgres://"):
        url = "postgresql://" + url[len("postgres://"):]
    if url.startswith("postgresql://"):
        url = "postgresql+psycopg://" + url[len("postgresql://"):]
    return url


def redact_url(url: str) -> str:
    try:
        parts = urlsplit(url)
    except ValueError:
        return "<unparseable>"
    if parts.password:
        netloc = parts.netloc.replace(f":{parts.password}@", ":***@")
        return urlunsplit(parts._replace(netloc=netloc))
    return url


@dataclass(frozen=True)
class Settings:
    database_url: str = "sqlite:///./ana_data/ana.db"
    shard_db: str = "./ana_data/shards.db"
    secret_key: str = field(default="", repr=False)
    dev: bool = False
    anthropic_key_present: bool = False
    use_llm: str = "auto"                      # auto | on | off
    allow_signups: bool = True
    invite_codes: tuple[str, ...] = field(default=(), repr=False)
    daily_cycles_per_user: int = 60
    daily_llm_cycles_total: int = 2000
    allowed_hosts: tuple[str, ...] = ("localhost", "127.0.0.1")
    public_origin: Optional[str] = None
    max_active_journeys: int = 3
    # extras (not in the spec table)
    max_cycles: int = 30
    runtime_cache_size: int = 64
    max_body_bytes: int = 32 * 1024
    act_per_minute: int = 30
    auth_per_ip_per_minute: int = 20
    auth_per_email_per_minute: int = 8        # FAILED logins per (email, client IP)
    auth_email_global_per_minute: int = 5     # FAILED logins per email, all IPs (refill)
    auth_email_global_burst: int = 50         # ... and its burst
    create_per_minute: int = 10               # journey creations per user
    identity_per_minute: int = 10             # PATCH /api/identity per user
    onboarding_per_minute: int = 5            # onboarding submissions per user
    safety_model_per_hour: int = 60           # model safety screens per user
    password_hash_concurrency: int = 2        # argon2 hash/verify at once (process-wide)
    password_hash_wait_seconds: float = 2.0   # queue wait before 503 + Retry-After
    # client IP (rate limits): a header set by the trusted edge proxy, else the
    # Nth-from-right X-Forwarded-For entry, else the socket peer.
    client_ip_header: Optional[str] = None
    trusted_proxy_hops: int = 0
    session_days: int = 30

    @classmethod
    def from_env(cls, env: Optional[Mapping[str, str]] = None) -> "Settings":
        env = os.environ if env is None else env
        dev = _bool(env.get("ANA_DEV"), False)
        secret = (env.get("ANA_SECRET_KEY") or "").strip()
        if not secret:
            if not dev:
                raise ConfigError("ANA_SECRET_KEY is required (set ANA_DEV=1 for local development)")
            secret = secrets.token_urlsafe(32)
            logger.warning("ANA_DEV=1 and no ANA_SECRET_KEY: using a random per-process key "
                           "(sessions will not survive a restart)")
        elif len(secret) < 16 and not dev:
            raise ConfigError("ANA_SECRET_KEY must be at least 16 characters")
        use_llm = (env.get("ANA_USE_LLM") or "auto").strip().lower()
        if use_llm not in ("auto", "on", "off"):
            raise ConfigError("ANA_USE_LLM must be auto, on or off")
        origin = (env.get("ANA_PUBLIC_ORIGIN") or "").strip().rstrip("/") or None
        if origin and not origin.startswith(("http://", "https://")):
            raise ConfigError("ANA_PUBLIC_ORIGIN must start with http:// or https://")
        hosts = _csv(env.get("ANA_ALLOWED_HOSTS")) or ["localhost", "127.0.0.1"]
        ip_header = (env.get("ANA_CLIENT_IP_HEADER") or "").strip().lower() or None
        if ip_header and not all(ch.isalnum() or ch == "-" for ch in ip_header):
            raise ConfigError("ANA_CLIENT_IP_HEADER must be a header name like fly-client-ip")
        return cls(
            database_url=normalise_db_url(env.get("ANA_DATABASE_URL") or cls.database_url),
            shard_db=env.get("ANA_SHARD_DB") or cls.shard_db,
            secret_key=secret,
            dev=dev,
            anthropic_key_present=bool((env.get("ANTHROPIC_API_KEY") or "").strip()),
            use_llm=use_llm,
            allow_signups=_bool(env.get("ANA_ALLOW_SIGNUPS"), True),
            invite_codes=tuple(_csv(env.get("ANA_INVITE_CODES"))),
            daily_cycles_per_user=_int(env, "ANA_DAILY_CYCLES_PER_USER", 60),
            daily_llm_cycles_total=_int(env, "ANA_DAILY_LLM_CYCLES_TOTAL", 2000),
            allowed_hosts=tuple(h.lower() for h in hosts),
            public_origin=origin,
            max_active_journeys=_int(env, "ANA_MAX_ACTIVE_JOURNEYS", 3, lo=1),
            max_cycles=_int(env, "ANA_MAX_CYCLES", 30, lo=1),
            runtime_cache_size=_int(env, "ANA_RUNTIME_CACHE_SIZE", 64, lo=1),
            act_per_minute=_int(env, "ANA_ACT_PER_MINUTE", 30, lo=1),
            safety_model_per_hour=_int(env, "ANA_SAFETY_MODEL_PER_HOUR", 60),
            password_hash_concurrency=_int(env, "ANA_PASSWORD_HASH_CONCURRENCY", 2, lo=1),
            client_ip_header=ip_header,
            trusted_proxy_hops=_int(env, "ANA_TRUSTED_PROXY_HOPS", 0),
        )

    def with_(self, **kw) -> "Settings":
        return replace(self, **kw)

    def validate(self) -> None:
        if not self.secret_key:
            raise ConfigError("a secret key is required")

    @property
    def secure_cookies(self) -> bool:
        if self.public_origin and self.public_origin.startswith("https://"):
            return True
        return not self.dev

    @property
    def host_allowlist(self) -> set[str]:
        hosts = {h.lower() for h in self.allowed_hosts}
        if self.public_origin:
            hosts.add((urlsplit(self.public_origin).hostname or "").lower())
        return {h for h in hosts if h}

    def describe(self) -> dict:
        """The effective configuration, safe to log (no secrets)."""
        out = {}
        for f in fields(self):
            v = getattr(self, f.name)
            if f.name == "secret_key":
                v = "<set>" if v else "<unset>"
            elif f.name == "invite_codes":
                v = f"<{len(v)} codes>" if v else "<none>"
            elif f.name == "database_url":
                v = redact_url(v)
            out[f.name] = v
        return out
