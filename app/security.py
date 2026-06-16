"""Slug validation, API-key auth, and browser session cookies.

Two ways to authenticate a write request:
  - `X-API-Key` / `Authorization: Bearer` header — for the CLI and agents.
  - A signed `hs_session` cookie (set by /api/login) — for the browser UI, so
    the key isn't re-entered on every action. Cookie auth additionally requires
    the `X-HS-Auth: 1` header, which (with SameSite=Strict) blocks CSRF.
"""

from __future__ import annotations

import base64
import hashlib
import hmac
import json
import re
import time
from typing import Annotated

from fastapi import HTTPException, Request, Security
from fastapi.security import APIKeyHeader

from .config import settings

_SLUG_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_-]{0,63}$")
RESERVED_SLUGS = frozenset(
    {"api", "healthz", "health", "robots.txt", "favicon.ico", "favicon.svg", "static"}
)
SLUG_RULE = "must match ^[A-Za-z0-9][A-Za-z0-9_-]{0,63}$ and not be a reserved word"

SESSION_COOKIE = "hs_session"


# --- slugs ---------------------------------------------------------------- #
def is_valid_slug(slug: str) -> bool:
    return bool(_SLUG_RE.match(slug)) and slug not in RESERVED_SLUGS


def validate_slug(slug: str) -> str:
    if not is_valid_slug(slug):
        raise HTTPException(status_code=400, detail=f"invalid slug: {SLUG_RULE}")
    return slug


# --- API key -------------------------------------------------------------- #
def _presented_key(x_api_key: str | None, authorization: str | None) -> str | None:
    if x_api_key:
        return x_api_key.strip()
    if authorization and authorization.lower().startswith("bearer "):
        return authorization[7:].strip()
    return None


def validate_api_key(key: str) -> bool:
    return bool(key) and any(hmac.compare_digest(key, k) for k in settings.api_keys)


def _header_key_ok(request: Request) -> bool:
    presented = _presented_key(
        request.headers.get("x-api-key"), request.headers.get("authorization")
    )
    return bool(presented) and any(
        hmac.compare_digest(presented, k) for k in settings.api_keys
    )


# --- session cookie ------------------------------------------------------- #
def _b64e(raw: bytes) -> str:
    return base64.urlsafe_b64encode(raw).rstrip(b"=").decode("ascii")


def _b64d(text: str) -> bytes:
    padding = "=" * (-len(text) % 4)  # re-add the '=' padding stripped by _b64e
    return base64.urlsafe_b64decode(text + padding)


def _sign(payload: str) -> str:
    digest = hmac.new(
        settings.session_secret, payload.encode("ascii"), hashlib.sha256
    ).digest()
    return _b64e(digest)


def make_session_token(now: float) -> str:
    claims = {"iat": int(now)}
    payload = _b64e(json.dumps(claims, separators=(",", ":")).encode("utf-8"))
    return f"{payload}.{_sign(payload)}"


def verify_session_token(token: str, now: float | None = None) -> bool:
    now = time.time() if now is None else now
    parts = token.split(".", 1)
    if len(parts) != 2:
        return False
    payload, sig = parts
    if not hmac.compare_digest(sig, _sign(payload)):
        return False
    try:
        claims = json.loads(_b64d(payload))
        iat = int(claims["iat"])
    except Exception:
        return False
    age = now - iat
    return -300 <= age <= settings.session_max_age  # small clock-skew tolerance


def session_cookie_valid(request: Request) -> bool:
    token = request.cookies.get(SESSION_COOKIE)
    return bool(token) and verify_session_token(token)


def _session_ok(request: Request) -> bool:
    # Cookie auth must carry a custom header. A cross-origin caller (e.g. a
    # sandboxed hosted page) can't set it without a CORS preflight we never
    # grant, which together with SameSite=Strict blocks CSRF.
    if request.headers.get("x-hs-auth") != "1":
        return False
    return session_cookie_valid(request)


# --- the dependency ------------------------------------------------------- #
# Docs-only: registers an "X-API-Key" scheme so /api/docs shows an Authorize
# button and marks the protected routes as secured. auto_error=False means it
# never raises on its own — the real allow/deny decision stays entirely in
# require_auth, which also accepts Authorization: Bearer and the session cookie.
api_key_scheme = APIKeyHeader(
    name="X-API-Key",
    auto_error=False,
    description=(
        "Upload API key. The browser UI may instead authenticate with the "
        "hs_session cookie plus an X-HS-Auth: 1 header."
    ),
)


def require_auth(
    request: Request,
    _key: Annotated[str | None, Security(api_key_scheme)] = None,
) -> None:
    """Allow a request only with a valid API key (header) or session (cookie).

    ``_key`` is intentionally unused — it exists only to attach the OpenAPI
    security scheme to routes that depend on this. The actual key check reads
    from the raw request below so the Bearer and cookie paths still work.
    """
    if settings.allow_anonymous and not settings.api_keys:
        return
    if _header_key_ok(request) or _session_ok(request):
        return
    raise HTTPException(
        status_code=401,
        detail="authentication required",
        headers={"WWW-Authenticate": "Bearer"},
    )
