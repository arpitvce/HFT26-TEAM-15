from __future__ import annotations

import os
import re
import shutil
import stat
import uuid
import zipfile
from datetime import datetime, timezone
from pathlib import Path, PurePosixPath
from typing import Any, BinaryIO

from repository_store import RepositoryStore

MAX_ZIP_UPLOAD_BYTES = int(os.getenv("REPOSITORY_MAX_ZIP_UPLOAD_BYTES", str(50 * 1024 * 1024)))
MAX_ZIP_EXTRACTED_BYTES = int(os.getenv("REPOSITORY_MAX_ZIP_EXTRACTED_BYTES", str(200 * 1024 * 1024)))
MAX_ZIP_FILES = int(os.getenv("REPOSITORY_MAX_ZIP_FILES", "10000"))
MAX_ZIP_ENTRIES = int(os.getenv("REPOSITORY_MAX_ZIP_ENTRIES", "20000"))


class IngestionError(Exception):
    def __init__(self, status_code: int, detail: str) -> None:
        super().__init__(detail)
        self.status_code = status_code
        self.detail = detail


def new_repository_id() -> str:
    return f"repo_{uuid.uuid4().hex}"


def utc_now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _validate_root_directory(path_text: str) -> Path:
    if not path_text or not path_text.strip():
        raise IngestionError(422, "Repository path must not be empty")
    candidate = Path(path_text).expanduser()
    if candidate.is_symlink():
        raise IngestionError(422, "Repository path must not be a symbolic link")
    try:
        path = candidate.resolve(strict=True)
    except FileNotFoundError as exc:
        raise IngestionError(404, "Repository directory does not exist") from exc
    except OSError as exc:
        raise IngestionError(422, "Repository path is invalid or inaccessible") from exc
    if not path.is_dir():
        raise IngestionError(422, "Repository path must point to a directory")
    try:
        with os.scandir(path) as entries:
            if next(entries, None) is None:
                raise IngestionError(422, "Repository directory is empty")
    except PermissionError as exc:
        raise IngestionError(403, "Repository directory is not readable") from exc
    except OSError as exc:
        raise IngestionError(422, "Repository directory cannot be read") from exc
    return path


def register_local_repository(path_text: str, store: RepositoryStore) -> dict[str, Any]:
    path = _validate_root_directory(path_text)
    repository_id = new_repository_id()
    name = path.name or path.anchor.rstrip("\\/") or "repository"
    store.create(repository_id, name, path, "local", utc_now())
    store.set_status(repository_id, "ready")
    record = store.get(repository_id)
    assert record is not None
    return record


def _safe_entry_parts(info: zipfile.ZipInfo) -> tuple[str, ...]:
    name = info.filename
    if not name or "\x00" in name or "\\" in name or ":" in name:
        raise IngestionError(400, "ZIP archive contains an unsafe path")
    if name.startswith("/") or re.match(r"^[A-Za-z]:", name):
        raise IngestionError(400, "ZIP archive contains an absolute path")
    normalized = name[:-1] if info.is_dir() and name.endswith("/") else name
    raw_parts = normalized.split("/")
    if not raw_parts or any(part in {"", ".", ".."} for part in raw_parts):
        raise IngestionError(400, "ZIP archive contains an unsafe path")
    parts = PurePosixPath(normalized).parts
    if not parts or any(part in {"", ".", ".."} for part in parts):
        raise IngestionError(400, "ZIP archive contains an unsafe path")
    reserved_names = {"CON", "PRN", "AUX", "NUL"} | {
        f"{prefix}{index}" for prefix in ("COM", "LPT") for index in range(1, 10)
    }
    for part in parts:
        windows_basename = part.split(".", 1)[0].rstrip(". ").upper()
        if part.endswith((".", " ")) or windows_basename in reserved_names:
            raise IngestionError(400, "ZIP archive contains an unsupported path component")
    if PurePosixPath(normalized).is_absolute():
        raise IngestionError(400, "ZIP archive contains an absolute path")

    mode = info.external_attr >> 16
    file_type = stat.S_IFMT(mode)
    if stat.S_ISLNK(mode):
        raise IngestionError(400, "ZIP archive contains a symbolic link")
    if file_type and file_type not in {stat.S_IFREG, stat.S_IFDIR}:
        raise IngestionError(400, "ZIP archive contains an unsupported file type")
    if info.is_dir() != (file_type == stat.S_IFDIR) and file_type:
        raise IngestionError(400, "ZIP archive contains inconsistent entry metadata")
    return parts


def _copy_upload(source: BinaryIO, destination: Path) -> int:
    copied = 0
    try:
        with destination.open("xb") as target:
            while chunk := source.read(1024 * 1024):
                copied += len(chunk)
                if copied > MAX_ZIP_UPLOAD_BYTES:
                    raise IngestionError(413, "ZIP upload exceeds the configured size limit")
                target.write(chunk)
    except IngestionError:
        raise
    except OSError as exc:
        raise IngestionError(400, "Could not read the uploaded ZIP archive") from exc
    if copied == 0:
        raise IngestionError(400, "ZIP archive is empty")
    return copied


def ingest_zip_archive(
    filename: str | None,
    source: BinaryIO,
    storage_root: Path,
    store: RepositoryStore,
) -> dict[str, Any]:
    if not filename or not filename.strip():
        raise IngestionError(422, "A ZIP filename is required")
    archive_basename = filename.replace("\\", "/").rsplit("/", 1)[-1]
    if Path(archive_basename).suffix.lower() != ".zip":
        raise IngestionError(415, "Only .zip archives are supported")
    name = Path(archive_basename).stem.strip()[:200] or "repository"

    repository_id = new_repository_id()
    final_path = storage_root / repository_id
    stage_path = storage_root / ".staging" / repository_id
    archive_path = stage_path / "upload.zip"
    content_path = stage_path / "content"
    store.create(repository_id, name, final_path, "zip", utc_now())

    try:
        stage_path.mkdir(parents=True, exist_ok=False)
        _copy_upload(source, archive_path)
        if not zipfile.is_zipfile(archive_path):
            raise IngestionError(400, "Uploaded file is not a valid ZIP archive")

        try:
            with zipfile.ZipFile(archive_path) as archive:
                entries = archive.infolist()
                if not entries:
                    raise IngestionError(400, "ZIP archive contains no repository files")
                if len(entries) > MAX_ZIP_ENTRIES:
                    raise IngestionError(413, "ZIP archive contains too many entries")

                seen: set[str] = set()
                total_declared_size = 0
                file_count = 0
                validated: list[tuple[zipfile.ZipInfo, tuple[str, ...]]] = []
                for info in entries:
                    parts = _safe_entry_parts(info)
                    key = "/".join(parts).casefold()
                    if key in seen:
                        raise IngestionError(400, "ZIP archive contains duplicate paths")
                    seen.add(key)
                    if not info.is_dir():
                        file_count += 1
                        total_declared_size += info.file_size
                        if file_count > MAX_ZIP_FILES:
                            raise IngestionError(413, "ZIP archive contains too many files")
                        if total_declared_size > MAX_ZIP_EXTRACTED_BYTES:
                            raise IngestionError(413, "ZIP archive expands beyond the configured size limit")
                    validated.append((info, parts))

                content_path.mkdir(parents=True, exist_ok=False)
                actual_size = 0
                for info, parts in validated:
                    target = content_path.joinpath(*parts)
                    if not target.resolve().is_relative_to(content_path.resolve()):
                        raise IngestionError(400, "ZIP archive contains an unsafe path")
                    if info.is_dir():
                        target.mkdir(parents=True, exist_ok=True)
                        continue
                    target.parent.mkdir(parents=True, exist_ok=True)
                    with archive.open(info, "r") as source_file, target.open("xb") as target_file:
                        while chunk := source_file.read(1024 * 1024):
                            actual_size += len(chunk)
                            if actual_size > MAX_ZIP_EXTRACTED_BYTES:
                                raise IngestionError(413, "ZIP archive expands beyond the configured size limit")
                            target_file.write(chunk)

                if file_count == 0:
                    raise IngestionError(400, "ZIP archive contains no repository files")
                os.replace(content_path, final_path)
        except zipfile.BadZipFile as exc:
            raise IngestionError(400, "ZIP archive is corrupted or invalid") from exc
        except (OSError, RuntimeError, NotImplementedError, ValueError) as exc:
            if isinstance(exc, IngestionError):
                raise
            raise IngestionError(400, "ZIP archive could not be safely extracted") from exc

        store.set_status(repository_id, "ready")
    except IngestionError as exc:
        store.set_status(repository_id, "failed", exc.detail)
        shutil.rmtree(stage_path, ignore_errors=True)
        shutil.rmtree(final_path, ignore_errors=True)
        raise
    except OSError as exc:
        store.set_status(repository_id, "failed", "Repository archive could not be stored")
        shutil.rmtree(stage_path, ignore_errors=True)
        shutil.rmtree(final_path, ignore_errors=True)
        raise IngestionError(500, "Repository archive could not be stored") from exc
    finally:
        shutil.rmtree(stage_path, ignore_errors=True)

    record = store.get(repository_id)
    assert record is not None
    return record
