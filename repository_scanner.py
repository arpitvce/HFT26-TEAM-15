from __future__ import annotations

import fnmatch
import os
import stat
from collections import Counter, defaultdict
from datetime import datetime, timezone
from pathlib import Path, PurePosixPath
from typing import Any

from repository_store import RepositoryStore

DEFAULT_EXCLUDED_DIRECTORIES = {
    ".git", "node_modules", "venv", ".venv", "__pycache__", "dist", "build",
    "coverage", ".next", ".cache",
}
EXCLUDED_DIRECTORY_NAMES = {
    name.strip().casefold()
    for name in os.getenv("REPOSITORY_EXCLUDED_DIRECTORIES", ",".join(sorted(DEFAULT_EXCLUDED_DIRECTORIES))).split(",")
    if name.strip()
}
DEFAULT_GENERATED_PATTERNS = {
    "*.min.js", "*.min.css", "*.map", "*.pyc", "*.pyo", "*.generated.*", "*_generated.*",
}
GENERATED_FILE_PATTERNS = tuple(
    pattern.strip().casefold()
    for pattern in os.getenv("REPOSITORY_GENERATED_PATTERNS", ",".join(sorted(DEFAULT_GENERATED_PATTERNS))).split(",")
    if pattern.strip()
)
MAX_FILE_SIZE_BYTES = int(os.getenv("REPOSITORY_MAX_FILE_SIZE_BYTES", str(1024 * 1024)))
MAX_SCAN_FILES = int(os.getenv("REPOSITORY_MAX_SCAN_FILES", "10000"))
MAX_SCAN_ENTRIES = int(os.getenv("REPOSITORY_MAX_SCAN_ENTRIES", "50000"))
BINARY_SNIFF_BYTES = 8192

LANGUAGE_BY_EXTENSION = {
    ".py": "Python",
    ".pyw": "Python",
    ".js": "JavaScript",
    ".mjs": "JavaScript",
    ".cjs": "JavaScript",
    ".jsx": "JavaScript",
    ".ts": "TypeScript",
    ".mts": "TypeScript",
    ".cts": "TypeScript",
    ".tsx": "TypeScript",
    ".java": "Java",
    ".c": "C",
    ".h": "C",
    ".cc": "C++",
    ".cpp": "C++",
    ".cxx": "C++",
    ".hpp": "C++",
    ".hh": "C++",
    ".hxx": "C++",
    ".cs": "C#",
    ".go": "Go",
    ".rs": "Rust",
    ".php": "PHP",
    ".rb": "Ruby",
    ".html": "HTML",
    ".htm": "HTML",
    ".css": "CSS",
    ".json": "JSON",
    ".yaml": "YAML",
    ".yml": "YAML",
    ".md": "Markdown",
    ".markdown": "Markdown",
    ".sql": "SQL",
}
SOURCE_LANGUAGES = {
    "Python", "JavaScript", "TypeScript", "Java", "C", "C++", "C#", "Go",
    "Rust", "PHP", "Ruby", "HTML", "CSS", "SQL",
}
BINARY_EXTENSIONS = {
    ".7z", ".avi", ".bin", ".bmp", ".class", ".dll", ".doc", ".docx", ".exe", ".gif",
    ".gz", ".ico", ".jpeg", ".jpg", ".mp3", ".mp4", ".pdf", ".png", ".so",
    ".svgz", ".tar", ".wasm", ".webp", ".woff", ".woff2", ".xls", ".xlsx", ".zip",
}


class RepositoryScanError(Exception):
    def __init__(self, status_code: int, detail: str) -> None:
        super().__init__(detail)
        self.status_code = status_code
        self.detail = detail


def detect_language(extension: str) -> str:
    return LANGUAGE_BY_EXTENSION.get(extension.casefold(), "unknown")


def is_hidden_name(name: str, file_stat: os.stat_result | None = None) -> bool:
    hidden_attribute = getattr(stat, "FILE_ATTRIBUTE_HIDDEN", 0x2)
    has_hidden_attribute = bool(
        file_stat is not None and getattr(file_stat, "st_file_attributes", 0) & hidden_attribute
    )
    return name.startswith(".") or has_hidden_attribute


def is_binary_file(path: Path, extension: str, size_bytes: int) -> bool:
    if extension.casefold() in BINARY_EXTENSIONS:
        return True
    if size_bytes > MAX_FILE_SIZE_BYTES:
        return False
    descriptor: int | None = None
    try:
        expected_stat = path.lstat()
        flags = os.O_RDONLY | getattr(os, "O_BINARY", 0) | getattr(os, "O_NOFOLLOW", 0)
        descriptor = os.open(path, flags)
        opened_stat = os.fstat(descriptor)
        if not stat.S_ISREG(opened_stat.st_mode) or not os.path.samestat(expected_stat, opened_stat):
            return True
        with os.fdopen(descriptor, "rb") as file_handle:
            descriptor = None
            sample = file_handle.read(BINARY_SNIFF_BYTES)
    except OSError:
        return False
    finally:
        if descriptor is not None:
            os.close(descriptor)
    if b"\x00" in sample:
        return True
    try:
        sample.decode("utf-8-sig")
    except UnicodeDecodeError:
        return True
    control_bytes = sum(byte < 32 and byte not in {9, 10, 12, 13} for byte in sample)
    return bool(sample) and control_bytes / len(sample) > 0.10


def _is_junction(path: Path) -> bool:
    is_junction = getattr(path, "is_junction", None)
    return bool(is_junction and is_junction())


def _modified_at(timestamp: float | None) -> str | None:
    if timestamp is None:
        return None
    return datetime.fromtimestamp(timestamp, timezone.utc).isoformat()


def resolve_repository_root(root_path: str) -> Path:
    candidate = Path(root_path)
    try:
        resolved = candidate.resolve(strict=True)
    except OSError as exc:
        raise RepositoryScanError(404, "Repository directory is missing or inaccessible") from exc
    # Phase 1 stores a canonical absolute path. A changed symlinked root is rejected.
    if os.path.normcase(str(candidate.absolute())) != os.path.normcase(str(resolved)):
        raise RepositoryScanError(400, "Repository root cannot be a symbolic link")
    if not resolved.is_dir():
        raise RepositoryScanError(400, "Repository root is not a directory")
    return resolved


def scan_repository_root(root: Path) -> tuple[list[dict[str, Any]], dict[str, Any], dict[str, int], dict[str, int]]:
    """Collect filesystem metadata only; file content is sampled only for binary detection."""
    entries: list[dict[str, Any]] = []
    warnings: list[str] = []
    total_files = total_directories = total_size = included_size = 0
    included_files = excluded_files = hidden_files = hidden_directories = 0
    binary_files = generated_files = unsupported_files = oversized_files = symlink_files = source_files = 0
    languages: Counter[str] = Counter()
    extensions: Counter[str] = Counter()

    def add_file_entry(
        path: Path,
        relative: Path,
        inherited_reason: str | None = None,
        symlink: bool = False,
    ) -> None:
        nonlocal total_files, total_size, included_size, included_files, excluded_files
        nonlocal hidden_files, binary_files, generated_files, unsupported_files, oversized_files
        nonlocal symlink_files, source_files
        name = path.name
        relative_path = relative.as_posix()
        extension = path.suffix.casefold()
        language = detect_language(extension)
        try:
            file_stat = path.lstat()
            size = file_stat.st_size
            modified_at = _modified_at(file_stat.st_mtime)
            unreadable = False
        except OSError:
            size = 0
            modified_at = None
            unreadable = True
            warnings.append(f"Could not read metadata for {relative_path}")
        hidden = is_hidden_name(name, file_stat if not unreadable else None)

        generated = any(
            fnmatch.fnmatchcase(relative_path.casefold(), pattern)
            or fnmatch.fnmatchcase(name.casefold(), pattern)
            for pattern in GENERATED_FILE_PATTERNS
        )
        oversized = size > MAX_FILE_SIZE_BYTES
        is_binary = False if symlink else (
            extension in BINARY_EXTENSIONS
            or (not inherited_reason and not generated and not oversized and is_binary_file(path, extension, size))
        )
        unsupported = language == "unknown" and not is_binary
        reason = inherited_reason
        if symlink:
            reason = reason or "symbolic_link_not_followed"
            symlink_files += 1
        elif unreadable:
            reason = reason or "unreadable_file"
        elif generated:
            reason = reason or "generated_file"
        elif oversized:
            reason = reason or f"file_exceeds_{MAX_FILE_SIZE_BYTES}_byte_limit"
        elif is_binary:
            reason = reason or "binary_file"
        elif unsupported:
            reason = reason or "unsupported_file_type"

        included = reason is None
        total_files += 1
        total_size += size
        extensions[extension or "(no extension)"] += 1
        if hidden:
            hidden_files += 1
        if is_binary:
            binary_files += 1
        if generated:
            generated_files += 1
        if unsupported:
            unsupported_files += 1
        if oversized:
            oversized_files += 1
        if included:
            included_files += 1
            included_size += size
            if language != "unknown":
                languages[language] += 1
            if language in SOURCE_LANGUAGES:
                source_files += 1
        else:
            excluded_files += 1

        entries.append({
            "path": relative_path,
            "name": name,
            "type": "symlink" if symlink else "file",
            "size_bytes": size,
            "extension": extension,
            "language": language,
            "modified_at": modified_at,
            "is_hidden": hidden,
            "is_included": included,
            "exclusion_reason": reason,
            "is_binary": is_binary,
            "is_generated": generated,
            "is_unsupported": unsupported,
            "is_oversized": oversized,
        })
        if total_files > MAX_SCAN_FILES:
            raise RepositoryScanError(413, f"Repository exceeds the {MAX_SCAN_FILES} file scan limit")

    def scan_directory(directory: Path, relative: Path, inherited_reason: str | None = None) -> None:
        nonlocal total_directories, hidden_directories
        try:
            with os.scandir(directory) as children:
                child_entries = []
                for child in children:
                    child_entries.append(child)
                    if len(entries) + len(child_entries) > MAX_SCAN_ENTRIES:
                        raise RepositoryScanError(
                            413, f"Repository exceeds the {MAX_SCAN_ENTRIES} scan entry limit"
                        )
                child_entries.sort(key=lambda item: (item.name.casefold(), item.name))
        except OSError:
            warnings.append(f"Could not read directory {relative.as_posix() or '.'}")
            return

        for child in child_entries:
            child_path = Path(child.path)
            child_relative = relative / child.name
            if child.is_symlink() or _is_junction(child_path):
                add_file_entry(child_path, child_relative, inherited_reason, symlink=True)
                continue
            try:
                is_directory = child.is_dir(follow_symlinks=False)
                is_regular_file = child.is_file(follow_symlinks=False)
            except OSError:
                warnings.append(f"Could not inspect {child_relative.as_posix()}")
                continue
            if is_directory:
                total_directories += 1
                directory_reason = inherited_reason
                if directory_reason is None and child.name.casefold() in EXCLUDED_DIRECTORY_NAMES:
                    directory_reason = f"excluded_directory:{child.name}"
                try:
                    directory_stat = child.stat(follow_symlinks=False)
                    modified_at = _modified_at(directory_stat.st_mtime)
                    hidden = is_hidden_name(child.name, directory_stat)
                except OSError:
                    modified_at = None
                    hidden = is_hidden_name(child.name)
                    warnings.append(f"Could not read metadata for directory {child_relative.as_posix()}")
                if hidden:
                    hidden_directories += 1
                entries.append({
                    "path": child_relative.as_posix(),
                    "name": child.name,
                    "type": "directory",
                    "size_bytes": None,
                    "extension": None,
                    "language": None,
                    "modified_at": modified_at,
                    "is_hidden": hidden,
                    "is_included": directory_reason is None,
                    "exclusion_reason": directory_reason,
                    "is_binary": False,
                    "is_generated": False,
                    "is_unsupported": False,
                    "is_oversized": False,
                })
                scan_directory(child_path, child_relative, directory_reason)
            elif is_regular_file:
                add_file_entry(child_path, child_relative, inherited_reason)
            # Sockets, devices, and other special filesystem entries are not followed or indexed.
            if len(entries) > MAX_SCAN_ENTRIES:
                raise RepositoryScanError(413, f"Repository exceeds the {MAX_SCAN_ENTRIES} scan entry limit")

    scan_directory(root, Path())
    statistics: dict[str, Any] = {
        "total_files": total_files,
        "total_directories": total_directories,
        "total_folders": total_directories,
        "included_files": included_files,
        "excluded_files": excluded_files,
        "total_repository_size_bytes": total_size,
        "total_size_bytes": total_size,
        "included_file_size_bytes": included_size,
        "source_files": source_files,
        "binary_files": binary_files,
        "generated_files": generated_files,
        "unsupported_files": unsupported_files,
        "oversized_files": oversized_files,
        "symlink_files": symlink_files,
        "hidden_files": hidden_files,
        "hidden_directories": hidden_directories,
        "scan_warnings": warnings[:100],
    }
    statistics["source_code_files"] = source_files
    statistics["total_excluded_files"] = excluded_files
    return entries, statistics, dict(sorted(languages.items())), dict(sorted(extensions.items()))


def build_tree(entries: list[dict[str, Any]]) -> list[dict[str, Any]]:
    children_by_parent: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for entry in entries:
        relative = PurePosixPath(entry["path"])
        parent = relative.parent.as_posix()
        if parent == ".":
            parent = ""
        node = {
            "name": entry["name"],
            "type": entry["type"],
            "path": entry["path"],
            "size_bytes": entry["size_bytes"],
            "extension": entry["extension"],
            "language": entry["language"],
            "modified_at": entry["modified_at"],
            "is_hidden": entry["is_hidden"],
            "is_included": entry["is_included"],
            "exclusion_reason": entry["exclusion_reason"],
            "is_binary": entry["is_binary"],
            "is_generated": entry["is_generated"],
            "is_unsupported": entry["is_unsupported"],
            "is_oversized": entry["is_oversized"],
        }
        children_by_parent[parent].append(node)

    def populate(parent: str) -> list[dict[str, Any]]:
        children = children_by_parent.get(parent, [])
        children.sort(key=lambda node: (node["type"] != "directory", node["name"].casefold(), node["name"]))
        for node in children:
            if node["type"] == "directory":
                node["children"] = populate(node["path"])
        return children

    return populate("")


def get_scan_summary(repository_id: str, repository_name: str, store: RepositoryStore) -> dict[str, Any]:
    scan = store.get_scan(repository_id)
    if scan is None or scan["status"] != "complete" or scan["statistics"] is None:
        raise RepositoryScanError(409, "Repository has not been scanned")
    entries = store.get_entries(repository_id)
    return {
        "success": True,
        "repository_id": repository_id,
        "repository_name": repository_name,
        "scan_status": scan["status"],
        "scanned_at": scan["scanned_at"],
        "statistics": scan["statistics"],
        "languages": scan["languages"],
        "extensions": scan["extensions"],
        "tree": build_tree(entries),
    }


def scan_registered_repository(
    repository_id: str,
    store: RepositoryStore,
    force: bool = False,
) -> dict[str, Any]:
    repository = store.get(repository_id)
    if repository is None:
        raise RepositoryScanError(404, "Repository not found")
    if repository["status"] != "ready":
        raise RepositoryScanError(409, "Repository is not ready to scan")

    previous = store.get_scan(repository_id)
    if not force and previous and previous["status"] == "complete":
        result = get_scan_summary(repository_id, repository["name"], store)
        result["cached"] = True
        return result
    if not store.begin_scan(repository_id):
        raise RepositoryScanError(409, "A scan is already in progress for this repository")

    try:
        root = resolve_repository_root(repository["root_path"])
        entries, statistics, languages, extensions = scan_repository_root(root)
        scanned_at = datetime.now(timezone.utc).isoformat()
        store.save_scan(repository_id, scanned_at, statistics, languages, extensions, entries)
    except RepositoryScanError as exc:
        store.fail_scan(repository_id, exc.detail)
        raise
    except OSError as exc:
        store.fail_scan(repository_id, "Repository scan failed because of a filesystem error")
        raise RepositoryScanError(500, "Repository scan failed because of a filesystem error") from exc

    result = get_scan_summary(repository_id, repository["name"], store)
    result["cached"] = False
    return result
