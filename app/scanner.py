from __future__ import annotations

import os
from pathlib import Path

from .catalog import ignored, infer_episode, preferences
from .config import settings
from .db import now, transaction


MEDIA_EXTENSIONS = {
    ".3gp",
    ".asf",
    ".avi",
    ".divx",
    ".flv",
    ".iso",
    ".m2ts",
    ".m4v",
    ".mkv",
    ".mov",
    ".mp4",
    ".mpeg",
    ".mpg",
    ".mts",
    ".ogm",
    ".rmvb",
    ".ts",
    ".vob",
    ".webm",
    ".wmv",
}


def classify(path: Path) -> str | None:
    if path.is_dir():
        if path.name.upper() == "VIDEO_TS":
            return "dvd"
        if path.name.upper() == "BDMV":
            return "bluray"
        return None
    if path.suffix.lower() == ".iso":
        return "iso"
    if path.suffix.lower() in MEDIA_EXTENSIONS:
        return "file"
    return None


def discover() -> dict[str, object]:
    """Inventory supported media without opening or probing any media file."""
    root = settings.ingest_dir
    if not root.exists():
        raise FileNotFoundError(f"Ingest directory does not exist: {root}")
    if not root.is_dir():
        raise NotADirectoryError(f"Ingest path is not a directory: {root}")

    try:
        with os.scandir(root) as entries:
            root_entry_count = sum(1 for _ in entries)
    except PermissionError as error:
        raise PermissionError(
            f"Cannot read ingest directory {root}. Grant container UID 568 "
            "read and execute access to the TrueNAS dataset."
        ) from error

    ignored_patterns = preferences()["ignored_patterns"]
    found: list[tuple[Path, str, dict[str, object]]] = []
    walk_errors: list[OSError] = []
    for current, directories, files in os.walk(
        root, onerror=walk_errors.append
    ):
        current_path = Path(current)
        current_relative = current_path.relative_to(root)
        directories[:] = [
            directory
            for directory in directories
            if not directory.startswith(".")
            and not ignored(current_relative / directory, ignored_patterns)
        ]

        disc_directories = [
            directory
            for directory in directories
            if directory.upper() in {"VIDEO_TS", "BDMV"}
        ]
        for directory in disc_directories:
            path = current_path / directory
            found.append(
                (path, classify(path) or "disc", infer_episode(path, root))
            )
            directories.remove(directory)

        for filename in files:
            path = current_path / filename
            if ignored(path.relative_to(root), ignored_patterns):
                continue
            kind = classify(path)
            if kind:
                found.append((path, kind, infer_episode(path, root)))

    timestamp = now()
    discovered_paths = {str(path.resolve()) for path, _, _ in found}
    new_count = 0
    updated_count = 0
    with transaction(immediate=True) as connection:
        connection.execute("UPDATE sources SET present=0, updated_at=?", (timestamp,))
        for path, kind, grouping in found:
            try:
                stat = path.stat()
            except OSError:
                continue
            # Do not recursively calculate directory sizes. That can open every
            # VOB/M2TS and make a network-mounted source look busy or locked.
            size = stat.st_size if path.is_file() else 0
            mtime = stat.st_mtime
            resolved = str(path.resolve())
            existing = connection.execute(
                "SELECT id, size, mtime FROM sources WHERE path=?", (resolved,)
            ).fetchone()
            values = (
                path.stem if path.is_file() else path.parent.name,
                kind,
                size,
                mtime,
                grouping["group_key"],
                grouping["series_name"],
                grouping["season_number"],
                grouping["episode_number"],
                grouping["episode_title"],
            )
            if existing is None:
                connection.execute(
                    """
                    INSERT INTO sources
                        (path, name, kind, size, mtime, present, probe_status,
                         group_key, series_name, season_number, episode_number,
                         episode_title, discovered_at, updated_at)
                    VALUES (?, ?, ?, ?, ?, 1, 'discovered', ?, ?, ?, ?, ?, ?, ?)
                    """,
                    (resolved, *values, timestamp, timestamp),
                )
                new_count += 1
            else:
                changed = existing["size"] != size or existing["mtime"] != mtime
                connection.execute(
                    """
                    UPDATE sources
                       SET name=?, kind=?, size=?, mtime=?, present=1,
                           group_key=?, series_name=?, season_number=?,
                           episode_number=?, episode_title=?,
                           probe_status=CASE WHEN ? THEN 'discovered'
                                             ELSE probe_status END,
                           metadata_json=CASE WHEN ? THEN NULL
                                              ELSE metadata_json END,
                           probe_error=CASE WHEN ? THEN NULL ELSE probe_error END,
                           updated_at=?
                     WHERE path=?
                    """,
                    (*values, changed, changed, changed, timestamp, resolved),
                )
                updated_count += 1

    warnings = [
        f"Skipped unreadable path: {error.filename or error}"
        for error in walk_errors[:10]
    ]
    if not found:
        if root_entry_count == 0:
            warnings.append(
                "The ingest mount is empty. In Portainer, bind a path that "
                "exists on the Docker host (normally /mnt/... on TrueNAS)."
            )
        else:
            warnings.append(
                f"The ingest mount contains {root_entry_count} top-level "
                "entries, but no supported media sources were found."
            )
    return {
        "found": len(discovered_paths),
        "new": new_count,
        "updated": updated_count,
        "root_entries": root_entry_count,
        "warnings": warnings,
    }
