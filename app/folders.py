"""SQLite folder membership; page bytes remain in the existing flat store."""

from __future__ import annotations

import sqlite3
from collections.abc import Iterator
from contextlib import contextmanager
from dataclasses import dataclass

from .config import settings


@dataclass(frozen=True)
class Folder:
    slug: str
    pages: list[str]


@contextmanager
def _connect() -> Iterator[sqlite3.Connection]:
    connection = sqlite3.connect(settings.data_dir / "folders.sqlite3", timeout=10)
    try:
        connection.execute("PRAGMA foreign_keys = ON")
        with connection:
            yield connection
    finally:
        connection.close()


def initialize() -> None:
    with _connect() as db:
        db.execute("CREATE TABLE IF NOT EXISTS folders (slug TEXT PRIMARY KEY)")
        db.execute(
            "CREATE TABLE IF NOT EXISTS folder_pages ("
            "page_slug TEXT PRIMARY KEY, folder_slug TEXT NOT NULL "
            "REFERENCES folders(slug) ON DELETE CASCADE)"
        )
        db.execute(
            "CREATE INDEX IF NOT EXISTS folder_pages_folder ON folder_pages(folder_slug)"
        )


def create(slug: str) -> bool:
    with _connect() as db:
        cursor = db.execute("INSERT OR IGNORE INTO folders(slug) VALUES (?)", (slug,))
        return cursor.rowcount == 1


def get(slug: str) -> Folder | None:
    with _connect() as db:
        if not db.execute("SELECT 1 FROM folders WHERE slug = ?", (slug,)).fetchone():
            return None
        pages = [
            row[0]
            for row in db.execute(
                "SELECT page_slug FROM folder_pages WHERE folder_slug = ? "
                "ORDER BY page_slug",
                (slug,),
            )
        ]
    return Folder(slug, pages)


def list_all() -> list[Folder]:
    with _connect() as db:
        slugs = [row[0] for row in db.execute("SELECT slug FROM folders ORDER BY slug")]
        rows = db.execute(
            "SELECT folder_slug, page_slug FROM folder_pages ORDER BY page_slug"
        ).fetchall()
    pages: dict[str, list[str]] = {slug: [] for slug in slugs}
    for folder_slug, page_slug in rows:
        pages[folder_slug].append(page_slug)
    return [Folder(slug, pages[slug]) for slug in slugs]


def delete(slug: str) -> bool:
    with _connect() as db:
        cursor = db.execute("DELETE FROM folders WHERE slug = ?", (slug,))
        return cursor.rowcount == 1


def assign(folder_slug: str, page_slug: str) -> bool:
    with _connect() as db:
        if not db.execute(
            "SELECT 1 FROM folders WHERE slug = ?", (folder_slug,)
        ).fetchone():
            return False
        db.execute(
            "INSERT INTO folder_pages(page_slug, folder_slug) VALUES (?, ?) "
            "ON CONFLICT(page_slug) DO UPDATE SET folder_slug = excluded.folder_slug",
            (page_slug, folder_slug),
        )
    return True


def remove(folder_slug: str, page_slug: str) -> bool:
    with _connect() as db:
        cursor = db.execute(
            "DELETE FROM folder_pages WHERE folder_slug = ? AND page_slug = ?",
            (folder_slug, page_slug),
        )
        return cursor.rowcount == 1


def remove_page(page_slug: str) -> None:
    with _connect() as db:
        db.execute("DELETE FROM folder_pages WHERE page_slug = ?", (page_slug,))
