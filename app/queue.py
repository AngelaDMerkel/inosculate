"""Validate explicit title choices and reserve a whole batch atomically."""

import json
from sqlite3 import Connection

from .catalog import LANGUAGES
from .config import settings
from .db import connect, now, row, transaction
from .util import ensure_beneath, json_load, safe_relative_mkv


def _prepared_job(source: dict, body: dict) -> dict:
    if not source or source["probe_status"] != "ready":
        raise ValueError("This source is not ready")
    output_kind = body.get("output_kind")
    if output_kind not in {"movie", "television"}:
        raise ValueError("Choose Movies or Television")
    relative = safe_relative_mkv(str(body.get("relative_path", "")))
    root = (
        settings.movies_dir if output_kind == "movie" else settings.television_dir
    )
    output = ensure_beneath(root / relative, root)
    if str(output) == source["path"]:
        raise ValueError("The output must be a new file, separate from the source")
    overwrite = bool(body.get("overwrite"))
    if output.exists() and not overwrite:
        raise FileExistsError(str(output))

    metadata = json_load(source["metadata_json"], {})
    if source["kind"] != "file" and not body.get("title_id"):
        raise ValueError("Choose a title for each disc")
    title_id = str(body.get("title_id", "0"))
    title = next(
        (
            value
            for value in metadata.get("titles", [])
            if str(value["id"]) == title_id
        ),
        None,
    )
    if not title:
        raise ValueError("Selected title was not found")
    available = {str(track["key"]) for track in title.get("tracks", [])}
    selected = list(
        dict.fromkeys(
            str(value)
            for value in body.get("selected_tracks", [])
            if str(value) in available
        )
    )
    if any(str(value) not in available for value in body.get("selected_tracks", [])):
        raise ValueError("Track choices have changed. Review this title again.")
    if not selected:
        raise ValueError("Select at least one track")
    selected_types = {
        track["type"]
        for track in title.get("tracks", [])
        if str(track["key"]) in selected
    }
    if not selected_types.intersection({"video", "audio"}):
        raise ValueError("Select at least one video or audio track")

    raw_overrides = body.get("track_overrides", {})
    if not isinstance(raw_overrides, dict):
        raise ValueError("Track overrides must be an object")
    language_codes = {code for code, _ in LANGUAGES}
    overrides: dict[str, dict[str, str]] = {}
    for track_key, value in raw_overrides.items():
        if str(track_key) not in available or not isinstance(value, dict):
            continue
        language = str(value.get("language", "")).strip().lower()
        name = "".join(c for c in str(value.get("name", "")).strip() if c.isprintable())[:180]
        if language and language not in language_codes:
            raise ValueError(f"Unsupported track language: {language}")
        changes = {}
        if language:
            changes["language"] = language
        if name:
            changes["name"] = name
        elif value.get("clear_name") is True:
            changes["clear_name"] = True
        if changes:
            overrides[str(track_key)] = changes
    return {
        "source_id": int(source["id"]),
        "title_id": title_id,
        "output_kind": output_kind,
        "relative_path": str(relative),
        "output_path": str(output),
        "selected_tracks": selected,
        "track_overrides": overrides,
        "overwrite": overwrite,
        "input": {"title": title, "mtime": source["mtime"], "size": source["size"]},
    }


def _insert_prepared_job(
    connection: Connection, prepared: dict, timestamp: str
) -> int:
    source = connection.execute("SELECT * FROM sources WHERE id=? AND present=1",
                                (prepared["source_id"],)).fetchone()
    if not source or source["queue_state"] not in {"awaiting", "deferred", "scheduled"}:
        raise ValueError("This source is no longer awaiting a queue decision")
    snapshot = prepared.get("input")
    if snapshot and (source["probe_status"] != "ready" or snapshot["mtime"] != source["mtime"]
                     or snapshot["size"] != source["size"]):
        raise ValueError("This source changed. Review it again.")
    conflict = connection.execute("""
        SELECT id FROM jobs WHERE status IN ('queued','running','awaiting_overwrite')
         AND (output_path=? OR (source_id=? AND title_id=?))
    """, (prepared["output_path"], source["id"], prepared["title_id"])).fetchone()
    if conflict:
        raise ValueError(f"The title or destination already has a job (#{conflict['id']})")
    connection.execute("UPDATE sources SET queue_state='scheduled', updated_at=? WHERE id=?",
                       (timestamp, source["id"]))
    cursor = connection.execute(
        """
        INSERT INTO jobs
            (source_id, title_id, output_kind, relative_path, output_path,
             selected_tracks_json, track_overrides_json,
             status, overwrite_approved, created_at, updated_at, input_json)
        VALUES (?, ?, ?, ?, ?, ?, ?, 'queued', ?, ?, ?, ?)
        """,
        (
            prepared["source_id"],
            prepared["title_id"],
            prepared["output_kind"],
            prepared["relative_path"],
            prepared["output_path"],
            json.dumps(prepared["selected_tracks"]),
            json.dumps(prepared["track_overrides"]),
            prepared["overwrite"],
            timestamp,
            timestamp,
            json.dumps(snapshot) if snapshot else None,
        ),
    )
    return int(cursor.lastrowid)


def schedule_batch(items: list[dict]) -> list[int]:
    if not isinstance(items, list) or not items:
        raise ValueError("Add at least one title to the batch")
    prepared = []
    for item in items:
        if not isinstance(item, dict):
            raise ValueError("Invalid batch item")
        source = row("SELECT * FROM sources WHERE id=? AND present=1", (int(item["source_id"]),))
        if not source:
            raise ValueError("Source is no longer available")
        if item.get("mtime", source["mtime"]) != source["mtime"]:
            raise ValueError(f"{source['name']} changed. Review the title again.")
        prepared.append(_prepared_job(source, item))
    with transaction(immediate=True) as connection:
        return [_insert_prepared_job(connection, job, now()) for job in prepared]


def activity(history: int) -> dict:
    history = max(1, min(2**31 - 1, history))
    summary = """SELECT jobs.id, title_id, relative_path, output_path, status,
                        phase, progress, cancel_requested, error, jobs.started_at, jobs.created_at, sources.name AS source_name
                   FROM jobs JOIN sources ON sources.id=jobs.source_id"""
    active = "('queued','running','awaiting_overwrite','retryable')"
    connection = connect()
    try:
        connection.execute("BEGIN")
        current = connection.execute(f"{summary} WHERE status IN {active} ORDER BY jobs.id").fetchall()
        past = connection.execute(f"{summary} WHERE status NOT IN {active} ORDER BY jobs.id DESC LIMIT ?",
                                  (history + 1,)).fetchall()
        return {"jobs": [dict(j) for j in [*current, *past[:history]]], "more": len(past) > history}
    finally:
        connection.close()
