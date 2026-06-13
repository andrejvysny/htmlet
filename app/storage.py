"""Filesystem storage. Each slug maps to ``{data_dir}/{slug}.html``.

Writes are atomic; auto-slugs are allocated race-safely with O_EXCL and an
in-process high-water mark so repeated auto-uploads don't rescan the directory.
"""

from __future__ import annotations

import contextlib
import os
import re
import tempfile
from dataclasses import dataclass
from pathlib import Path

from .config import settings


class StorageFull(Exception):
    """Raised when the configured MAX_FILES limit would be exceeded."""


@dataclass(frozen=True)
class StoredFile:
    slug: str
    size: int
    modified: float


# Cached highest auto-slug number for this process. None until first scan.
_auto_high: int | None = None


def _path_for(slug: str) -> Path:
    """Resolve the on-disk path for a slug, refusing anything outside the data dir."""
    path = (settings.data_dir / f"{slug}.html").resolve()
    if path.parent != settings.data_dir:
        raise ValueError(f"refusing path outside data dir: {path}")
    return path


def path_if_exists(slug: str) -> Path | None:
    path = _path_for(slug)
    return path if path.is_file() else None


def exists(slug: str) -> bool:
    return _path_for(slug).is_file()


def read(slug: str) -> bytes | None:
    path = path_if_exists(slug)
    return path.read_bytes() if path else None


def count_files() -> int:
    n = 0
    with os.scandir(settings.data_dir) as entries:
        for entry in entries:
            if entry.name.endswith(".html") and entry.is_file():
                n += 1
    return n


def _ensure_capacity() -> None:
    if settings.max_files and count_files() >= settings.max_files:
        raise StorageFull(f"file limit reached ({settings.max_files})")


def save_new(slug: str, data: bytes) -> bool:
    """Atomically create a new file. Return False if the slug already exists.

    Raises StorageFull if the file-count limit is reached.
    """
    _ensure_capacity()
    path = _path_for(slug)
    try:
        fd = os.open(path, os.O_CREAT | os.O_EXCL | os.O_WRONLY, 0o644)
    except FileExistsError:
        return False
    with os.fdopen(fd, "wb") as handle:
        handle.write(data)
    return True


def save_overwrite(slug: str, data: bytes) -> None:
    """Atomically create-or-replace a file (write to tmp, then rename).

    Enforces the file-count limit only when creating a new slug.
    """
    if not exists(slug):
        _ensure_capacity()
    path = _path_for(slug)
    fd, tmp = tempfile.mkstemp(dir=str(settings.data_dir), suffix=".tmp")
    try:
        with os.fdopen(fd, "wb") as handle:
            handle.write(data)
        os.replace(tmp, path)
    except BaseException:
        _silent_unlink(Path(tmp))
        raise


def _scan_auto_high(prefix: str) -> int:
    pattern = re.compile(rf"^{re.escape(prefix)}-(\d+)$")
    highest = 0
    for entry in settings.data_dir.glob(f"{prefix}-*.html"):
        match = pattern.match(entry.stem)
        if match:
            highest = max(highest, int(match.group(1)))
    return highest


def save_auto(data: bytes) -> str:
    """Save under the next free ``{prefix}-N`` slug and return it.

    Uses a cached high-water mark for O(1) amortized allocation; the O_EXCL
    create handles any cache staleness or cross-process races by retrying.
    """
    global _auto_high
    prefix = settings.slug_prefix
    if _auto_high is None:
        _auto_high = _scan_auto_high(prefix)
    candidate = _auto_high + 1
    while True:
        slug = f"{prefix}-{candidate}"
        if save_new(slug, data):  # may raise StorageFull
            _auto_high = candidate
            return slug
        candidate += 1  # slug taken (stale cache / race); try the next number


def delete(slug: str) -> bool:
    try:
        _path_for(slug).unlink()
        return True
    except FileNotFoundError:
        return False


def list_files(
    limit: int | None = None, offset: int = 0
) -> tuple[list[StoredFile], int]:
    """Return (page_of_files, total_count). Only the returned page is stat()ed."""
    names = sorted(settings.data_dir.glob("*.html"))
    total = len(names)
    page = names[offset : offset + limit] if limit is not None else names[offset:]
    files: list[StoredFile] = []
    for entry in page:
        stat = entry.stat()
        files.append(
            StoredFile(slug=entry.stem, size=stat.st_size, modified=stat.st_mtime)
        )
    return files, total


def _silent_unlink(path: Path) -> None:
    with contextlib.suppress(FileNotFoundError):
        path.unlink()
