from __future__ import annotations

import sqlite3
from contextlib import contextmanager
from datetime import UTC, datetime
from pathlib import Path
from typing import Any, Iterator

from .config import settings


SCHEMA = """
CREATE TABLE IF NOT EXISTS sources (
    id INTEGER PRIMARY KEY,
    path TEXT NOT NULL UNIQUE,
    name TEXT NOT NULL,
    kind TEXT NOT NULL,
    size INTEGER NOT NULL DEFAULT 0,
    mtime REAL NOT NULL DEFAULT 0,
    present INTEGER NOT NULL DEFAULT 1,
    probe_status TEXT NOT NULL DEFAULT 'discovered',
    probe_error TEXT,
    metadata_json TEXT,
    group_key TEXT,
    series_name TEXT,
    season_number INTEGER,
    episode_number INTEGER,
    episode_title TEXT,
    queue_state TEXT NOT NULL DEFAULT 'awaiting',
    discovered_at TEXT NOT NULL,
    updated_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS jobs (
    id INTEGER PRIMARY KEY,
    source_id INTEGER NOT NULL REFERENCES sources(id),
    title_id TEXT NOT NULL DEFAULT '0',
    output_kind TEXT NOT NULL,
    relative_path TEXT NOT NULL,
    output_path TEXT NOT NULL,
    selected_tracks_json TEXT NOT NULL,
    track_overrides_json TEXT NOT NULL DEFAULT '{}',
    status TEXT NOT NULL DEFAULT 'queued',
    progress REAL NOT NULL DEFAULT 0,
    phase TEXT,
    log TEXT NOT NULL DEFAULT '',
    cancel_requested INTEGER NOT NULL DEFAULT 0,
    overwrite_approved INTEGER NOT NULL DEFAULT 0,
    attempts INTEGER NOT NULL DEFAULT 0,
    error TEXT,
    created_at TEXT NOT NULL,
    started_at TEXT,
    finished_at TEXT,
    updated_at TEXT NOT NULL
);

CREATE INDEX IF NOT EXISTS jobs_status_idx ON jobs(status, created_at);
CREATE INDEX IF NOT EXISTS jobs_source_idx ON jobs(source_id, id);

CREATE TABLE IF NOT EXISTS previews (
    id INTEGER PRIMARY KEY,
    source_id INTEGER NOT NULL REFERENCES sources(id),
    title_id TEXT NOT NULL DEFAULT '0',
    track_key TEXT NOT NULL,
    preview_type TEXT NOT NULL DEFAULT 'audio',
    status TEXT NOT NULL DEFAULT 'queued',
    path TEXT,
    result_json TEXT,
    error TEXT,
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL
);

CREATE INDEX IF NOT EXISTS previews_status_idx ON previews(status, created_at);

CREATE TABLE IF NOT EXISTS cleanup (
    id INTEGER PRIMARY KEY,
    job_id INTEGER NOT NULL UNIQUE REFERENCES jobs(id),
    source_id INTEGER NOT NULL REFERENCES sources(id),
    status TEXT NOT NULL DEFAULT 'pending',
    decision TEXT,
    error TEXT,
    created_at TEXT NOT NULL,
    decided_at TEXT
);

CREATE TABLE IF NOT EXISTS preferences (
    key TEXT PRIMARY KEY,
    value_json TEXT NOT NULL,
    updated_at TEXT NOT NULL
);
"""


def now() -> str:
    return datetime.now(UTC).isoformat()


def connect() -> sqlite3.Connection:
    connection = sqlite3.connect(
        settings.database_path, timeout=30, isolation_level=None
    )
    connection.row_factory = sqlite3.Row
    connection.execute("PRAGMA foreign_keys = ON")
    connection.execute("PRAGMA journal_mode = WAL")
    connection.execute("PRAGMA busy_timeout = 30000")
    return connection


@contextmanager
def transaction(immediate: bool = False) -> Iterator[sqlite3.Connection]:
    connection = connect()
    try:
        connection.execute("BEGIN IMMEDIATE" if immediate else "BEGIN")
        yield connection
        connection.commit()
    except Exception:
        connection.rollback()
        raise
    finally:
        connection.close()


def initialize() -> None:
    for path in (settings.data_dir, settings.preview_dir, settings.work_dir):
        Path(path).mkdir(parents=True, exist_ok=True)
    connection = connect()
    try:
        connection.executescript(SCHEMA)
        _ensure_column(connection, "sources", "group_key", "TEXT")
        _ensure_column(connection, "sources", "series_name", "TEXT")
        _ensure_column(connection, "sources", "season_number", "INTEGER")
        _ensure_column(connection, "sources", "episode_number", "INTEGER")
        _ensure_column(connection, "sources", "episode_title", "TEXT")
        _ensure_column(
            connection,
            "sources",
            "queue_state",
            "TEXT NOT NULL DEFAULT 'awaiting'",
        )
        _ensure_column(
            connection,
            "jobs",
            "track_overrides_json",
            "TEXT NOT NULL DEFAULT '{}'",
        )
        _ensure_column(
            connection,
            "previews",
            "preview_type",
            "TEXT NOT NULL DEFAULT 'audio'",
        )
        _ensure_column(connection, "previews", "result_json", "TEXT")
        connection.execute(
            """
            UPDATE sources
               SET probe_status='discovered'
             WHERE probe_status IN ('pending', 'waiting', 'probing')
            """
        )
        connection.execute(
            """
            UPDATE sources
               SET queue_state='scheduled'
             WHERE queue_state='awaiting'
               AND EXISTS (SELECT 1 FROM jobs WHERE jobs.source_id=sources.id)
            """
        )
    finally:
        connection.close()


def _ensure_column(
    connection: sqlite3.Connection,
    table: str,
    column: str,
    definition: str,
) -> None:
    existing = {
        value["name"]
        for value in connection.execute(f"PRAGMA table_info({table})")
    }
    if column not in existing:
        connection.execute(
            f"ALTER TABLE {table} ADD COLUMN {column} {definition}"
        )


def rows(query: str, parameters: tuple[Any, ...] = ()) -> list[dict[str, Any]]:
    connection = connect()
    try:
        return [dict(row) for row in connection.execute(query, parameters)]
    finally:
        connection.close()


def row(query: str, parameters: tuple[Any, ...] = ()) -> dict[str, Any] | None:
    found = rows(query, parameters)
    return found[0] if found else None
