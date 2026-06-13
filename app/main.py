"""FastAPI app: token-gated HTML upload, public clean-URL serving."""

from __future__ import annotations

import json
import time
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from pathlib import Path
from typing import Annotated
from urllib.parse import parse_qs

from fastapi import APIRouter, Depends, FastAPI, HTTPException, Query, Request, Response
from fastapi.responses import HTMLResponse, PlainTextResponse
from starlette.concurrency import run_in_threadpool
from starlette.datastructures import UploadFile

from . import storage
from .config import settings
from .models import (
    DeleteResult,
    FileEntry,
    FileList,
    FileRef,
    OkResult,
    SessionStatus,
    UploadResult,
)
from .security import (
    SESSION_COOKIE,
    is_valid_slug,
    make_session_token,
    require_auth,
    session_cookie_valid,
    validate_api_key,
    validate_slug,
)

try:  # Starlette's multipart size/part-count guard raises this.
    from starlette.formparsers import MultiPartException
except ImportError:  # pragma: no cover - defensive against version differences

    class MultiPartException(Exception):
        pass


_TEMPLATE = Path(__file__).parent / "templates" / "index.html"
_FAVICON = Path(__file__).parent / "static" / "favicon.svg"

# CSP for the first-party admin UI (NOT the sandbox CSP used for hosted pages).
# Allows its own inline script/style, Google Fonts, and same-origin fetches.
_INDEX_CSP = (
    "default-src 'self'; "
    "script-src 'self' 'unsafe-inline'; "
    "style-src 'self' 'unsafe-inline' https://fonts.googleapis.com; "
    "font-src https://fonts.gstatic.com; "
    "img-src 'self' data:; "
    "connect-src 'self'; "
    "base-uri 'none'; "
    "form-action 'self'; "
    "frame-ancestors 'none'"
)


@asynccontextmanager
async def lifespan(_: FastAPI) -> AsyncIterator[None]:
    settings.validate()
    yield


app = FastAPI(
    title="htmlet",
    summary="Host single HTML files at clean public URLs with token-gated uploads.",
    version="0.1.0",
    lifespan=lifespan,
    docs_url="/api/docs",
    redoc_url=None,
    openapi_url="/api/openapi.json",
)

# Public API routes (no auth): login/logout/session.
public_api = APIRouter(prefix="/api", tags=["auth"])
# Protected API routes: auth is enforced once here, at the router level, instead
# of repeating dependencies=[Depends(require_auth)] on every write endpoint.
files_api = APIRouter(
    prefix="/api", tags=["files"], dependencies=[Depends(require_auth)]
)


# --------------------------------------------------------------------------- #
# Helpers
# --------------------------------------------------------------------------- #
def _public_base(request: Request) -> str:
    if settings.base_url:
        return settings.base_url
    return str(request.base_url).rstrip("/")


def _url(request: Request, slug: str) -> str:
    return f"{_public_base(request)}/{slug}"


def _reject_oversized_content_length(request: Request) -> None:
    content_length = request.headers.get("content-length")
    if (
        content_length
        and content_length.isdigit()
        and int(content_length) > settings.max_upload_bytes
    ):
        raise HTTPException(
            413, f"payload too large (limit {settings.max_upload_bytes} bytes)"
        )


# An API key is tiny; bound the one unauthenticated body endpoint (/api/login) so
# a large body can't be buffered into memory before auth (see _extract_login_key).
_LOGIN_MAX_BODY = 64 * 1024


async def _read_body_capped(request: Request, cap: int | None = None) -> bytes:
    cap = settings.max_upload_bytes if cap is None else cap
    total = 0
    chunks: list[bytes] = []
    async for chunk in request.stream():
        total += len(chunk)
        if total > cap:
            raise HTTPException(413, f"payload too large (limit {cap} bytes)")
        chunks.append(chunk)
    return b"".join(chunks)


async def _extract_upload(request: Request) -> tuple[bytes, str | None]:
    """Read upload content + optional slug.

    Accepts multipart/form-data (``file`` field, optional ``slug`` field) OR a
    raw request body. Slug may also arrive via the ``slug`` query param or
    ``Slug`` header. Returns ``(content_bytes, slug_or_none)``.
    """
    _reject_oversized_content_length(request)
    content_type = request.headers.get("content-type", "")
    payload_slug: str | None = None

    if content_type.startswith("multipart/form-data"):
        # Bound each part to the configured limit (Starlette defaults to 1 MiB,
        # which would otherwise silently reject 1-5 MiB web-UI uploads) and cap
        # the number of parts so a multipart body can't buffer unbounded memory.
        try:
            form = await request.form(
                max_part_size=settings.max_upload_bytes, max_files=1, max_fields=2
            )
        except MultiPartException:
            raise HTTPException(
                413,
                f"multipart upload too large or malformed (limit {settings.max_upload_bytes} bytes)",
            )
        upload = form.get("file")
        if not isinstance(upload, UploadFile):
            raise HTTPException(400, "multipart upload must include a 'file' field")
        data = await upload.read()
        if len(data) > settings.max_upload_bytes:
            raise HTTPException(
                413, f"payload too large (limit {settings.max_upload_bytes} bytes)"
            )
        form_slug = form.get("slug")
        if isinstance(form_slug, str) and form_slug.strip():
            payload_slug = form_slug.strip()
    else:
        data = await _read_body_capped(request)

    slug = (
        payload_slug or request.query_params.get("slug") or request.headers.get("slug")
    )
    slug = slug.strip() if isinstance(slug, str) and slug.strip() else None
    return data, slug


def _serve_headers() -> dict[str, str]:
    headers = {
        "X-Content-Type-Options": "nosniff",
        "Referrer-Policy": "no-referrer",
    }
    if settings.serve_cache_control:
        headers["Cache-Control"] = settings.serve_cache_control
    if settings.content_security_policy:
        headers["Content-Security-Policy"] = settings.content_security_policy
    return headers


async def _extract_login_key(request: Request) -> str | None:
    """Read the API key from an X-API-Key header, JSON `{key}`, or a form field.

    The body is read with a tight cap (`_LOGIN_MAX_BODY`): this is the only
    unauthenticated body-reading endpoint, so it must not buffer an attacker's
    arbitrarily large body into memory.
    """
    if request.headers.get("x-api-key"):
        return request.headers["x-api-key"].strip()
    content_type = request.headers.get("content-type", "")
    if content_type.startswith("application/json"):
        raw = await _read_body_capped(request, _LOGIN_MAX_BODY)
        try:
            body = json.loads(raw or b"{}")
        except (json.JSONDecodeError, UnicodeDecodeError):
            return None
        key = body.get("key") if isinstance(body, dict) else None
        return key.strip() if isinstance(key, str) else None
    if content_type.startswith("application/x-www-form-urlencoded"):
        raw = await _read_body_capped(request, _LOGIN_MAX_BODY)
        values = parse_qs(raw.decode("utf-8", "replace"))
        key = values.get("key", [None])[0]
        return key.strip() if isinstance(key, str) else None
    if content_type.startswith("multipart/form-data"):
        # Multipart login is unusual; bound each part tightly and allow no files.
        try:
            form = await request.form(
                max_part_size=_LOGIN_MAX_BODY, max_files=0, max_fields=2
            )
        except MultiPartException:
            raise HTTPException(413, "login payload too large")
        key = form.get("key")
        return key.strip() if isinstance(key, str) else None
    return None


def _set_session_cookie(response: Response, request: Request) -> None:
    response.set_cookie(
        SESSION_COOKIE,
        make_session_token(time.time()),
        max_age=settings.session_max_age,
        httponly=True,
        samesite="strict",
        secure=request.url.scheme == "https",
        path="/",
    )


# --------------------------------------------------------------------------- #
# System routes (registered on the app, before the public catch-all)
# --------------------------------------------------------------------------- #
@app.get("/", response_class=HTMLResponse, include_in_schema=False)
def index() -> HTMLResponse:
    return HTMLResponse(
        _TEMPLATE.read_text(encoding="utf-8"),
        headers={
            "Cache-Control": "no-store",
            "X-Content-Type-Options": "nosniff",
            "Referrer-Policy": "no-referrer",
            "Content-Security-Policy": _INDEX_CSP,
        },
    )


@app.get("/healthz", response_class=PlainTextResponse, include_in_schema=False)
def healthz() -> str:
    return "ok"


# One SVG favicon serves the whole instance. /favicon.svg is referenced by the
# admin UI; /favicon.ico catches browsers' implicit request (and gives hosted
# pages a default icon, which their own <link rel="icon"> can override). SVG is
# read per-request to mirror the index template (edits show without a restart).
@app.get("/favicon.ico", include_in_schema=False)
@app.get("/favicon.svg", include_in_schema=False)
def favicon() -> Response:
    return Response(
        content=_FAVICON.read_bytes(),
        media_type="image/svg+xml",
        headers={
            "Cache-Control": "public, max-age=86400",
            "X-Content-Type-Options": "nosniff",
        },
    )


@app.get("/robots.txt", response_class=PlainTextResponse, include_in_schema=False)
def robots() -> str:
    return "User-agent: *\nDisallow: /api/\n"


# --------------------------------------------------------------------------- #
# Public API (no auth)
# --------------------------------------------------------------------------- #
@public_api.post("/login")
async def login(request: Request, response: Response) -> OkResult:
    """Exchange a valid API key for a session cookie (used by the browser UI)."""
    key = await _extract_login_key(request)
    if not key or not validate_api_key(key):
        raise HTTPException(401, "invalid API key")
    _set_session_cookie(response, request)
    return OkResult()


@public_api.post("/logout")
def logout(response: Response) -> OkResult:
    response.delete_cookie(SESSION_COOKIE, path="/")
    return OkResult()


@public_api.get("/session")
def session(request: Request) -> SessionStatus:
    """Report whether this browser has a valid session (no auth required)."""
    if settings.allow_anonymous and not settings.api_keys:
        return SessionStatus(authenticated=True, anonymous=True)
    return SessionStatus(authenticated=session_cookie_valid(request))


# --------------------------------------------------------------------------- #
# Protected API (auth enforced at the files_api router level)
# --------------------------------------------------------------------------- #
@files_api.post("/upload", status_code=201)
async def upload(request: Request) -> UploadResult:
    """Upload HTML. Slug optional — auto-generated (`html-N`) if omitted."""
    data, slug = await _extract_upload(request)
    if not data:
        raise HTTPException(400, "empty upload")
    try:
        if slug:
            validate_slug(slug)
            # Storage does blocking disk I/O; run it off the event loop.
            if not await run_in_threadpool(storage.save_new, slug, data):
                raise HTTPException(409, f"slug '{slug}' already exists")
            final = slug
        else:
            final = await run_in_threadpool(storage.save_auto, data)
    except storage.StorageFull as exc:
        raise HTTPException(507, str(exc))
    return UploadResult(
        slug=final, url=_url(request, final), size=len(data), created=True
    )


@files_api.put("/files/{slug}")
async def put_file(slug: str, request: Request) -> FileRef:
    """Create or replace the HTML at an explicit slug (idempotent upsert)."""
    validate_slug(slug)
    data, _ = await _extract_upload(request)
    if not data:
        raise HTTPException(400, "empty upload")
    try:
        await run_in_threadpool(storage.save_overwrite, slug, data)
    except storage.StorageFull as exc:
        raise HTTPException(507, str(exc))
    # PUT is an idempotent upsert; 200 is correct whether or not it pre-existed
    # (a post-write existence check can't be made atomic, so we don't guess).
    return FileRef(slug=slug, url=_url(request, slug), size=len(data))


@files_api.get("/files")
def list_files(
    request: Request,
    limit: Annotated[int, Query(ge=1, le=1000)] = 1000,
    offset: Annotated[int, Query(ge=0)] = 0,
) -> FileList:
    base = _public_base(request)
    page, total = storage.list_files(limit=limit, offset=offset)
    return FileList(
        total=total,
        count=len(page),
        offset=offset,
        limit=limit,
        files=[
            FileEntry(
                slug=f.slug, url=f"{base}/{f.slug}", size=f.size, modified=f.modified
            )
            for f in page
        ],
    )


@files_api.delete("/files/{slug}")
def delete_file(slug: str) -> DeleteResult:
    validate_slug(slug)
    if not storage.delete(slug):
        raise HTTPException(404, "not found")
    return DeleteResult(deleted=slug)


# Register the API routers BEFORE the catch-all below so /api/* is never shadowed.
app.include_router(public_api)
app.include_router(files_api)


# --------------------------------------------------------------------------- #
# Public catch-all — MUST stay last (after the include_router calls above) so it
# never shadows the routes above. (RESERVED_SLUGS is a second line of defense.)
# --------------------------------------------------------------------------- #
@app.get("/{name}", include_in_schema=False)
def serve(name: str) -> Response:
    # Both /html-1 and /html-1.html resolve to the same page (.html is canonical
    # on disk; the bare slug is the canonical URL returned by uploads).
    slug = name[:-5] if name.endswith(".html") else name
    if not is_valid_slug(slug):
        raise HTTPException(404, "not found")
    # Eager read (file is <= MAX_UPLOAD_BYTES): avoids a lazy-open window where a
    # concurrent DELETE would turn the streamed response into a 500 instead of 404.
    data = storage.read(slug)
    if data is None:
        raise HTTPException(404, "not found")
    return Response(
        content=data, media_type="text/html; charset=utf-8", headers=_serve_headers()
    )
