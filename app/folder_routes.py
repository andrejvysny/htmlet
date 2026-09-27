"""Protected folder management and public folder page picker."""

from __future__ import annotations

from html import escape
from pathlib import Path

from fastapi import APIRouter, Depends, HTTPException, Request
from fastapi.responses import HTMLResponse

from . import folders, storage
from .config import settings
from .models import DeleteResult, FileRef, FolderCreate, FolderEntry, FolderList
from .security import is_valid_slug, require_auth, validate_slug

api = APIRouter(
    prefix="/api/folders", tags=["folders"], dependencies=[Depends(require_auth)]
)
public = APIRouter()
_TEMPLATE = Path(__file__).parent / "templates" / "folder.html"


def _entry(request: Request, folder: folders.Folder) -> FolderEntry:
    base = settings.base_url or str(request.base_url).rstrip("/")
    pages = []
    for slug in folder.pages:
        path = storage.path_if_exists(slug)
        if path is not None:
            pages.append(
                FileRef(slug=slug, url=f"{base}/{slug}", size=path.stat().st_size)
            )
    return FolderEntry(slug=folder.slug, url=f"{base}/f/{folder.slug}", pages=pages)


def _required_folder(slug: str) -> folders.Folder:
    folder = folders.get(slug)
    if folder is None:
        raise HTTPException(404, "folder not found")
    return folder


@api.post("", status_code=201)
def create_folder(body: FolderCreate, request: Request) -> FolderEntry:
    validate_slug(body.slug)
    if not folders.create(body.slug):
        raise HTTPException(409, f"folder '{body.slug}' already exists")
    return _entry(request, folders.Folder(body.slug, []))


@api.get("")
def list_folders(request: Request) -> FolderList:
    return FolderList(
        folders=[_entry(request, folder) for folder in folders.list_all()]
    )


@api.delete("/{slug}")
def delete_folder(slug: str) -> DeleteResult:
    validate_slug(slug)
    if not folders.delete(slug):
        raise HTTPException(404, "folder not found")
    return DeleteResult(deleted=slug)


@api.put("/{slug}/pages/{page_slug}")
def add_folder_page(slug: str, page_slug: str, request: Request) -> FolderEntry:
    validate_slug(slug)
    validate_slug(page_slug)
    if not storage.exists(page_slug):
        raise HTTPException(404, "page not found")
    if not folders.assign(slug, page_slug):
        raise HTTPException(404, "folder not found")
    return _entry(request, _required_folder(slug))


@api.delete("/{slug}/pages/{page_slug}")
def remove_folder_page(slug: str, page_slug: str, request: Request) -> FolderEntry:
    validate_slug(slug)
    validate_slug(page_slug)
    if not folders.remove(slug, page_slug):
        raise HTTPException(404, "page not in folder")
    return _entry(request, _required_folder(slug))


@public.get("/f/{slug}", response_class=HTMLResponse, include_in_schema=False)
def serve_folder(slug: str) -> HTMLResponse:
    if not is_valid_slug(slug):
        raise HTTPException(404, "folder not found")
    folder = _required_folder(slug)
    pages = [page for page in folder.pages if storage.exists(page)]
    items = "".join(
        f'<li><a href="/{escape(page, quote=True)}">'
        f'<span>{escape(page)}</span><span aria-hidden="true">↗</span></a></li>'
        for page in pages
    )
    if not items:
        items = '<li class="empty">No pages in this folder yet.</li>'
    content = (
        _TEMPLATE.read_text(encoding="utf-8")
        .replace("{{FOLDER}}", escape(slug))
        .replace("{{COUNT}}", str(len(pages)))
        .replace("{{PAGES}}", items)
    )
    return HTMLResponse(
        content,
        headers={
            "Cache-Control": "no-store",
            "X-Content-Type-Options": "nosniff",
            "Referrer-Policy": "no-referrer",
            "Content-Security-Policy": (
                "default-src 'none'; style-src 'unsafe-inline'; "
                "script-src 'unsafe-inline'; base-uri 'none'; frame-ancestors 'none'"
            ),
        },
    )
