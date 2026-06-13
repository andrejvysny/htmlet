"""Pydantic response models — the typed contracts for the JSON API.

Declaring these as path-operation return types lets FastAPI validate, document
(in ``/api/docs``), and serialize responses with pydantic-core (in Rust), which
is faster than hand-built ``JSONResponse`` payloads.

Every field here is part of the public wire contract consumed by the browser UI
(``app/templates/index.html``) and the stdlib CLI client (``client/htmlet.py``),
so do not rename or drop fields, and do not enable ``exclude_unset``/``exclude_none``.
"""

from __future__ import annotations

from pydantic import BaseModel


class UploadResult(BaseModel):
    slug: str
    url: str
    size: int
    created: bool


class FileRef(BaseModel):
    slug: str
    url: str
    size: int


class FileEntry(BaseModel):
    slug: str
    url: str
    size: int
    modified: float


class FileList(BaseModel):
    total: int
    count: int
    offset: int
    limit: int
    files: list[FileEntry]


class DeleteResult(BaseModel):
    deleted: str


class SessionStatus(BaseModel):
    authenticated: bool
    anonymous: bool = False


class OkResult(BaseModel):
    ok: bool = True
