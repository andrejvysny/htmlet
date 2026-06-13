"""Runtime configuration, read from environment variables at import time."""

from __future__ import annotations

import hashlib
import os
from pathlib import Path

DEFAULT_MAX_UPLOAD_BYTES = 5 * 1024 * 1024  # 5 MiB
DEFAULT_MAX_FILES = 10_000
DEFAULT_SESSION_MAX_AGE = 30 * 24 * 3600  # 30 days
DEFAULT_CSP = (
    "sandbox allow-scripts allow-forms allow-popups allow-modals allow-downloads"
)
DEFAULT_CACHE_CONTROL = "public, max-age=60"

# An API key is the only thing between the internet and write access, so refuse to
# start on an obviously-weak one. Generate a real key with `openssl rand -hex 32`.
MIN_API_KEY_LENGTH = 16
_WEAK_API_KEYS = frozenset(
    {
        "change-me-please",
        "change-me",
        "changeme",
        "change_me",
        "replace-me",
        "password",
        "secret",
        "admin",
        "test",
        "token",
        "apikey",
        "api-key",
        "api_key",
        "your-api-key",
        "your-upload-token",
        "key",
        "default",
    }
)


def _parse_keys(raw: str) -> set[str]:
    return {part.strip() for part in raw.split(",") if part.strip()}


def _parse_bool(raw: str) -> bool:
    return raw.strip().lower() in {"1", "true", "yes", "on"}


def _int_env(name: str, default: int) -> int:
    raw = os.environ.get(name)
    if raw is None or raw.strip() == "":
        return default
    try:
        return int(raw)
    except ValueError:
        raise RuntimeError(f"{name} must be an integer (got {raw!r}).")


class Settings:
    """All tunables. Instantiated once as the module-level ``settings`` singleton."""

    def __init__(self) -> None:
        self.data_dir: Path = Path(os.environ.get("DATA_DIR", "data")).resolve()
        # API_KEYS (preferred) or API_KEY (single) — comma-separated, any one grants write access.
        raw_api_keys = os.environ.get("API_KEYS", "") or os.environ.get("API_KEY", "")
        self.api_keys: set[str] = _parse_keys(raw_api_keys)
        self.allow_anonymous: bool = _parse_bool(
            os.environ.get("ALLOW_ANONYMOUS_UPLOAD", "")
        )
        self.max_upload_bytes: int = _int_env(
            "MAX_UPLOAD_BYTES", DEFAULT_MAX_UPLOAD_BYTES
        )
        self.max_files: int = _int_env("MAX_FILES", DEFAULT_MAX_FILES)
        self.slug_prefix: str = os.environ.get("SLUG_PREFIX", "html").strip() or "html"
        self.base_url: str = os.environ.get("BASE_URL", "").rstrip("/")
        self.serve_cache_control: str = os.environ.get(
            "SERVE_CACHE_CONTROL", DEFAULT_CACHE_CONTROL
        )
        self.content_security_policy: str = os.environ.get(
            "CONTENT_SECURITY_POLICY", DEFAULT_CSP
        )
        # Browser-session signing. Defaults to a secret derived from the API keys,
        # so rotating keys invalidates existing UI sessions. Override to keep
        # sessions stable across key changes.
        self.session_max_age: int = _int_env("SESSION_MAX_AGE", DEFAULT_SESSION_MAX_AGE)
        self.session_secret: bytes = self._derive_session_secret()

    def _reject_weak_api_keys(self) -> None:
        """Fail closed on placeholder or too-short keys (e.g. the shipped default)."""
        for key in self.api_keys:
            if key.lower() in _WEAK_API_KEYS or len(key) < MIN_API_KEY_LENGTH:
                raise RuntimeError(
                    "Refusing to start: a weak or placeholder API key is configured "
                    f"(keys must be at least {MIN_API_KEY_LENGTH} chars and not a known "
                    "placeholder like 'change-me-please'). Generate a strong one with "
                    "`openssl rand -hex 32` (or `htmlet genkey`) and set it in API_KEYS."
                )

    def _derive_session_secret(self) -> bytes:
        raw = os.environ.get("SESSION_SECRET", "").strip()
        if raw:
            return raw.encode("utf-8")
        material = "htmlet-session::" + "\n".join(sorted(self.api_keys))
        return hashlib.sha256(material.encode("utf-8")).digest()

    def validate(self) -> None:
        """Fail closed at startup if misconfigured; ensure the data dir exists."""
        if not self.api_keys and not self.allow_anonymous:
            raise RuntimeError(
                "Refusing to start: no upload auth configured. "
                "Set API_KEYS to a comma-separated list of secrets "
                "(generate one with `openssl rand -hex 32`), or set "
                "ALLOW_ANONYMOUS_UPLOAD=true to explicitly run an open instance "
                "(not recommended — anyone could publish HTML/JS on your domain)."
            )
        self._reject_weak_api_keys()
        if self.max_upload_bytes <= 0:
            raise RuntimeError("MAX_UPLOAD_BYTES must be a positive integer.")
        if self.max_files <= 0:
            raise RuntimeError("MAX_FILES must be a positive integer.")
        if self.session_max_age <= 0:
            raise RuntimeError("SESSION_MAX_AGE must be a positive integer.")
        prefix_body = self.slug_prefix.replace("-", "").replace("_", "")
        if not prefix_body.isalnum():
            raise RuntimeError(
                "SLUG_PREFIX must be alphanumeric (dashes/underscores allowed)."
            )
        self.data_dir.mkdir(parents=True, exist_ok=True)


settings = Settings()
